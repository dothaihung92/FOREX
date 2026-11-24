//+------------------------------------------------------------------+
//| GoldBot_FixedCapitalRisk.mq5                       version 2.01   |
//| XAUUSD M5 trend-pullback EA, ported from the Python gold_bot      |
//| project (gold_bot/strategy.py + risk_manager.py).                 |
//|                                                                    |
//| VERSION 2.00 - brought back in line with the backtested strategy. |
//| Version 1.00 had drifted from it and was trading a DIFFERENT       |
//| system from the one the backtest numbers describe:                 |
//|   - stop/target were 2.0/2.5 ATR; the strategy uses 1.8/3.0        |
//|   - only hour 9 was excluded; the strategy excludes 7 and 9        |
//|   - the H1 trend confirmation was missing                          |
//|   - the ATR-expansion filter was missing                           |
//|   - MT5's iATR is a SIMPLE average of true range; the strategy     |
//|     uses Wilder's smoothing, so every stop distance differed       |
//|   - MT5's iMACD signal line is an SMA; the strategy uses an EMA,   |
//|     so the MACD condition fired on different bars                  |
//| ATR, RSI, EMA and MACD are now computed here from closed bars with |
//| the same recurrences as gold_bot/indicators.py                    |
//| (tests/test_ea_parity.py checks those recurrences against it).    |
//|                                                                    |
//| NEW in 2.00 - price-action confirmation: the signal candle must    |
//| close in the top 30% of its own range for a buy (bottom 30% for a  |
//| sell). On five years of real XAUUSD M5 with full costs this        |
//| improved BOTH 2020-22 and 2023-25, and every threshold from 0.55   |
//| to 0.9 improved both - a broad effect, not a tuned peak:           |
//|                                                                    |
//|               trades  win    PF    5-yr return  max DD             |
//|   without        115  54.8%  2.05    +60.9%    -10.0%              |
//|   with            97  56.7%  2.39    +65.9%     -7.4%              |
//|                                                                    |
//|   with, trailing stop OFF (the default since v2.01)               |
//|                   97  50.5%  1.56    +57.1%    -17.3%             |
//|   (OFF roughly doubles drawdown; set InpUseTrailingStop = true to  |
//|   get the "with" row back)                                         |
//|                                                                    |
//| (full spread charged; $500 base, 2% risk. Earlier README figures   |
//| charged only half the spread and were overstated.)                 |
//|                                                                    |
//| Sizing: "fixed_capital_percent_risk" - lot from InpRiskPercent of  |
//| a FIXED InpBaseEquity, never live equity, normalised by the stop   |
//| distance. A winning streak cannot inflate the lot before a losing  |
//| streak hits it (that is what blew up the equity_step version).     |
//|                                                                    |
//| NOT COMPILED IN METATRADER - no Windows/MT5 in the authoring       |
//| environment. Strategy Tester first, then a demo account.          |
//+------------------------------------------------------------------+
#property copyright "gold_bot project"
#property version   "2.01"
#property strict

#include <Trade\Trade.mqh>

//=== Strategy inputs (defaults match config/config.yaml) =============
input group "=== Strategy ==="
input int      InpEmaFast              = 50;
input int      InpEmaSlow              = 100;
input int      InpRsiPeriod            = 14;
input double   InpRsiOversold          = 30.0;
input double   InpRsiOverbought        = 70.0;
input double   InpRsiPullback          = 50.0;
input int      InpAtrPeriod            = 14;
input double   InpAtrSlMult            = 1.8;      // 5:3 reward:risk - README "R:R ratio"
input double   InpAtrTpMult            = 3.0;
input ENUM_TIMEFRAMES InpHtfTimeframe  = PERIOD_M15;
input int      InpHtfEmaPeriod         = 100;
input bool     InpRequireHtf2          = true;     // H1 must agree too - README "Confluence filters"
input ENUM_TIMEFRAMES InpHtf2Timeframe = PERIOD_H1;
input int      InpHtf2EmaPeriod        = 100;
input bool     InpRequireAtrExpansion  = true;     // ATR above its own 20-bar mean
input int      InpAtrExpansionPeriod   = 20;
input double   InpMinClosePosition     = 0.7;      // price action: close in top 30% of range (0 = off)
input double   InpMinTrendStrengthPct  = 0.0256;   // skip entries when |EMAfast-EMAslow|/close*100 is below this
input string   InpExcludedHoursUtc     = "7,9";    // comma-separated UTC hours to skip

input group "=== Sessions (UTC) ==="
input string   InpSession1Start        = "07:00";
input string   InpSession1End          = "11:00";
input string   InpSession2Start        = "12:30";
input string   InpSession2End          = "16:00";
input int      InpBrokerUtcOffsetHours = 0;        // broker server time minus UTC (check your broker!)

input group "=== Risk / fixed_capital_percent_risk sizing ==="
input double   InpBaseEquity           = 500.0;    // FIXED capital used for sizing - never the live account equity
input double   InpRiskPercentPerTrade  = 2.0;      // % of InpBaseEquity risked per trade
input bool     InpUseTrailingStop      = false;    // OFF by request; true halves the drawdown (README)
input double   InpTrailingAtrMult      = 1.0;
input int      InpMaxTradesPerDay      = 4;
input double   InpMaxDailyLossPct      = 3.0;      // circuit breaker, against InpBaseEquity
input int      InpMaxConcurrentTrades  = 1;
input int      InpMagicNumber          = 20240502;
input int      InpSlippagePoints       = 50;

CTrade trade;

int hEmaHtf  = INVALID_HANDLE;
int hEmaHtf2 = INVALID_HANDLE;

datetime g_lastBarTime = 0;
int      g_excludedHours[];

// Indicator arrays over the last HIST closed M5 bars, oldest first.
// The last element (HIST-1) is the bar that just closed ("bar 1").
#define HIST 600
double g_o[], g_h[], g_l[], g_c[];
double g_emaFast[], g_emaSlow[], g_rsi[], g_atr[], g_hist[];
datetime g_t[];

//+------------------------------------------------------------------+
int OnInit()
{
   hEmaHtf  = iMA(_Symbol, InpHtfTimeframe,  InpHtfEmaPeriod,  0, MODE_EMA, PRICE_CLOSE);
   hEmaHtf2 = iMA(_Symbol, InpHtf2Timeframe, InpHtf2EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(hEmaHtf==INVALID_HANDLE || hEmaHtf2==INVALID_HANDLE)
   {
      Print("GoldBot: failed to create a higher-timeframe EMA handle");
      return(INIT_FAILED);
   }
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   ParseExcludedHours();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hEmaHtf);
   IndicatorRelease(hEmaHtf2);
}

//+------------------------------------------------------------------+
//| Indicators, with the same recurrences as gold_bot/indicators.py:  |
//|   EMA  : pandas ewm(span, adjust=False), seeded with the first    |
//|          value                                                    |
//|   RSI  : Wilder (ewm alpha=1/period) of gains and losses          |
//|   ATR  : Wilder (ewm alpha=1/period) of true range                |
//|   MACD : EMA12-EMA26, signal = EMA9 of that (NOT an SMA, which is  |
//|          what MT5's iMACD uses), histogram = macd - signal        |
//| 600 bars of history make the seed's influence negligible.         |
//+------------------------------------------------------------------+
void EmaSeries(const double &x[], int period, double &out[])
{
   int n = ArraySize(x);
   ArrayResize(out, n);
   double a = 2.0/(period+1.0);
   out[0] = x[0];
   for(int i=1; i<n; i++) out[i] = a*x[i] + (1.0-a)*out[i-1];
}

void WilderRsi(const double &c[], int period, double &out[])
{
   int n = ArraySize(c);
   ArrayResize(out, n);
   double a = 1.0/period, ag = 0.0, al = 0.0;
   out[0] = 50.0;
   for(int i=1; i<n; i++)
   {
      double d = c[i]-c[i-1];
      double g = d>0 ? d : 0.0, l = d<0 ? -d : 0.0;
      if(i==1) { ag = g; al = l; }
      else     { ag = a*g + (1.0-a)*ag; al = a*l + (1.0-a)*al; }
      out[i] = (al==0.0) ? 100.0 : 100.0 - 100.0/(1.0 + ag/al);
   }
}

void WilderAtr(const double &h[], const double &l[], const double &c[], int period, double &out[])
{
   int n = ArraySize(c);
   ArrayResize(out, n);
   double a = 1.0/period;
   out[0] = h[0]-l[0];
   for(int i=1; i<n; i++)
   {
      double tr = MathMax(h[i]-l[i], MathMax(MathAbs(h[i]-c[i-1]), MathAbs(l[i]-c[i-1])));
      out[i] = a*tr + (1.0-a)*out[i-1];
   }
}

bool LoadM5()
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   if(CopyRates(_Symbol, PERIOD_M5, 1, HIST, r) < HIST) return false;   // shift 1: closed bars only
   ArrayResize(g_o,HIST); ArrayResize(g_h,HIST); ArrayResize(g_l,HIST); ArrayResize(g_c,HIST); ArrayResize(g_t,HIST);
   for(int i=0; i<HIST; i++) { g_o[i]=r[i].open; g_h[i]=r[i].high; g_l[i]=r[i].low; g_c[i]=r[i].close; g_t[i]=r[i].time; }
   EmaSeries(g_c, InpEmaFast, g_emaFast);
   EmaSeries(g_c, InpEmaSlow, g_emaSlow);
   WilderRsi(g_c, InpRsiPeriod, g_rsi);
   WilderAtr(g_h, g_l, g_c, InpAtrPeriod, g_atr);
   double e12[], e26[], macd[], sig[];
   EmaSeries(g_c, 12, e12); EmaSeries(g_c, 26, e26);
   ArrayResize(macd, HIST);
   for(int i=0; i<HIST; i++) macd[i] = e12[i]-e26[i];
   EmaSeries(macd, 9, sig);
   ArrayResize(g_hist, HIST);
   for(int i=0; i<HIST; i++) g_hist[i] = macd[i]-sig[i];
   return true;
}

//+------------------------------------------------------------------+
//| Higher-timeframe trend as the backtest sees it: the HTF bar BEFORE |
//| the one that contains the signal bar (only fully closed HTF bars). |
//+------------------------------------------------------------------+
int HtfTrend(ENUM_TIMEFRAMES tf, int handle, datetime signalBarTime)
{
   int sh = iBarShift(_Symbol, tf, signalBarTime, false);
   if(sh < 0) return 0;
   sh += 1;
   double e[]; ArraySetAsSeries(e, true);
   if(CopyBuffer(handle, 0, sh, 1, e) < 1) return 0;
   double c = iClose(_Symbol, tf, sh);
   if(c <= 0) return 0;
   if(c > e[0]) return 1;
   if(c < e[0]) return -1;
   return 0;
}

//+------------------------------------------------------------------+
void ParseExcludedHours()
{
   string parts[];
   int n = StringSplit(InpExcludedHoursUtc, ',', parts);
   ArrayResize(g_excludedHours, n);
   for(int i=0; i<n; i++)
      g_excludedHours[i] = (int)StringToInteger(parts[i]);
}

bool IsHourExcluded(int hour)
{
   for(int i=0; i<ArraySize(g_excludedHours); i++)
      if(g_excludedHours[i]==hour) return true;
   return false;
}

bool ParseHHMM(const string s, int &h, int &m)
{
   string parts[];
   int n = StringSplit(s, ':', parts);
   if(n!=2) return false;
   h = (int)StringToInteger(parts[0]);
   m = (int)StringToInteger(parts[1]);
   return true;
}

//+------------------------------------------------------------------+
//| True if the given (already UTC-adjusted) time falls in either     |
//| configured session window.                                        |
//+------------------------------------------------------------------+
bool InSession(datetime tUtc)
{
   MqlDateTime dt;
   TimeToStruct(tUtc, dt);
   int curMinutes = dt.hour*60 + dt.min;

   int h1s,m1s,h1e,m1e,h2s,m2s,h2e,m2e;
   ParseHHMM(InpSession1Start,h1s,m1s);
   ParseHHMM(InpSession1End,h1e,m1e);
   ParseHHMM(InpSession2Start,h2s,m2s);
   ParseHHMM(InpSession2End,h2e,m2e);

   int s1=h1s*60+m1s, e1=h1e*60+m1e;
   int s2=h2s*60+m2s, e2=h2e*60+m2e;

   bool in1 = (s1<=e1) ? (curMinutes>=s1 && curMinutes<=e1) : (curMinutes>=s1 || curMinutes<=e1);
   bool in2 = (s2<=e2) ? (curMinutes>=s2 && curMinutes<=e2) : (curMinutes>=s2 || curMinutes<=e2);
   return in1 || in2;
}

//+------------------------------------------------------------------+
//| fixed_capital_percent_risk lot sizing - same math as              |
//| gold_bot/risk_manager.py's _position_size_lots_percent_risk(),     |
//| except the reference capital is ALWAYS InpBaseEquity, never the   |
//| live AccountInfoDouble(ACCOUNT_EQUITY). This is the whole point:   |
//| a winning streak must never inflate the lot size before a losing   |
//| streak hits it (see file header comment for the real-account       |
//| blowup this replaced).                                             |
//+------------------------------------------------------------------+
double GetLots(double entryPrice, double stopPrice)
{
   double stopDistance = MathAbs(entryPrice - stopPrice);
   if(stopDistance <= 0) return 0.0;

   double contractSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(contractSize <= 0) contractSize = 100.0; // XAUUSD standard lot = 100 oz fallback

   double riskAmount = InpRiskPercentPerTrade/100.0 * InpBaseEquity;
   double lossPerLot = stopDistance * contractSize;
   if(lossPerLot <= 0) return 0.0;

   double lots = riskAmount / lossPerLot;

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(lotStep > 0)
      lots = MathRound(lots/lotStep) * lotStep;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   return lots;
}

//+------------------------------------------------------------------+
//| Sum today's realized P&L and count of trade entries for this EA,  |
//| reconstructed from history so it survives EA/terminal restarts.   |
//+------------------------------------------------------------------+
void GetDailyStats(double &realizedPnl, int &tradesOpened)
{
   realizedPnl = 0.0;
   tradesOpened = 0;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour=0; dt.min=0; dt.sec=0;
   datetime dayStart = StructToTime(dt);

   if(!HistorySelect(dayStart, TimeCurrent())) return;

   int total = HistoryDealsTotal();
   for(int i=0; i<total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket==0) continue;
      if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol) continue;

      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)
         tradesOpened++;
      else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
         realizedPnl += HistoryDealGetDouble(ticket, DEAL_PROFIT) + HistoryDealGetDouble(ticket, DEAL_SWAP) + HistoryDealGetDouble(ticket, DEAL_COMMISSION);
   }
}

int CountOpenPositions()
{
   int count=0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket==0) continue;
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetString(POSITION_SYMBOL)==_Symbol && (long)PositionGetInteger(POSITION_MAGIC)==InpMagicNumber)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Daily-loss circuit breaker uses InpBaseEquity too (not live        |
//| equity) so a winning streak can't quietly raise the $ loss the     |
//| account is allowed to take before trading stops for the day.       |
//+------------------------------------------------------------------+
bool CanOpenTrade()
{
   if(InpBaseEquity <= 0) return false;

   if(CountOpenPositions() >= InpMaxConcurrentTrades) return false;

   double dailyPnl; int tradesToday;
   GetDailyStats(dailyPnl, tradesToday);

   if(tradesToday >= InpMaxTradesPerDay) return false;

   double maxLoss = -MathAbs(InpMaxDailyLossPct)/100.0 * InpBaseEquity;
   if(dailyPnl <= maxLoss) return false;

   return true;
}

//+------------------------------------------------------------------+
//| Trailing stop, as the backtest does it: after each closed bar the  |
//| stop moves to close - mult x ATR (Wilder) if that is better.       |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   if(!InpUseTrailingStop) return;
   int k = HIST-1;
   double atr1 = g_atr[k], close1 = g_c[k];
   if(atr1 <= 0) return;

   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket==0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) continue;

      int direction = (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY) ? 1 : -1;
      double curSl = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      double newSl = NormalizeDouble(close1 - direction*InpTrailingAtrMult*atr1, _Digits);
      bool better = (direction==1) ? (newSl > curSl) : (curSl==0 || newSl < curSl);
      if(better && !trade.PositionModify(ticket, newSl, tp))
         PrintFormat("trailing modify failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
void CheckEntrySignal()
{
   if(!CanOpenTrade()) return;
   if(CountOpenPositions() > 0) return;

   int k = HIST-1;                              // bar 1 = the bar that just closed
   double close1 = g_c[k], high1 = g_h[k], low1 = g_l[k];
   double emaFast1 = g_emaFast[k], emaSlow1 = g_emaSlow[k];
   double rsi1 = g_rsi[k], rsi2 = g_rsi[k-1];
   double atr1 = g_atr[k];
   if(atr1 <= 0) return;

   datetime bar1TimeServer = g_t[k];
   int htfTrend  = HtfTrend(InpHtfTimeframe,  hEmaHtf,  bar1TimeServer);
   int htf2Trend = HtfTrend(InpHtf2Timeframe, hEmaHtf2, bar1TimeServer);

   bool uptrend   = (close1 > emaSlow1) && (htfTrend > 0);
   bool downtrend = (close1 < emaSlow1) && (htfTrend < 0);

   // RSI touched near-extreme on any of the 6 bars strictly before bar 1.
   bool recentOversold=false, recentOverbought=false;
   for(int i=k-6; i<=k-1; i++)
   {
      if(g_rsi[i] <= InpRsiOversold+5)   recentOversold=true;
      if(g_rsi[i] >= InpRsiOverbought-5) recentOverbought=true;
   }
   bool rsiCrossUp   = (rsi1 > InpRsiPullback) && (rsi2 <= InpRsiPullback);
   bool rsiCrossDown = (rsi1 < (100-InpRsiPullback)) && (rsi2 >= (100-InpRsiPullback));
   bool macdRising   = g_hist[k] > g_hist[k-1];
   bool macdFalling  = g_hist[k] < g_hist[k-1];

   datetime bar1TimeUtc = bar1TimeServer - InpBrokerUtcOffsetHours*3600;
   bool sessionOk = InSession(bar1TimeUtc);
   MqlDateTime dts; TimeToStruct(bar1TimeUtc, dts);
   bool hourOk = !IsHourExcluded(dts.hour);

   bool trendEstablished = MathAbs(emaFast1-emaSlow1)/close1*100.0 > InpMinTrendStrengthPct;

   bool htf2Long  = !InpRequireHtf2 || htf2Trend > 0;
   bool htf2Short = !InpRequireHtf2 || htf2Trend < 0;

   bool atrExpanding = true;
   if(InpRequireAtrExpansion)
   {
      double sum = 0.0;
      for(int i=k-InpAtrExpansionPeriod+1; i<=k; i++) sum += g_atr[i];   // includes bar 1, as rolling().mean() does
      atrExpanding = atr1 > sum/InpAtrExpansionPeriod;
   }

   // Price action: where bar 1 closed within its own range (0 = low, 1 = high).
   bool closeLongOk = true, closeShortOk = true;
   if(InpMinClosePosition > 0)
   {
      double rng = high1 - low1;
      if(rng <= 0) { closeLongOk = false; closeShortOk = false; }
      else
      {
         double pos = (close1 - low1)/rng;
         closeLongOk  = pos >= InpMinClosePosition;
         closeShortOk = (1.0 - pos) >= InpMinClosePosition;
      }
   }

   bool longSignal = uptrend && recentOversold && rsiCrossUp && macdRising && (close1 > emaFast1) &&
                     sessionOk && hourOk && trendEstablished && htf2Long && atrExpanding && closeLongOk;
   bool shortSignal = downtrend && recentOverbought && rsiCrossDown && macdFalling && (close1 < emaFast1) &&
                      sessionOk && hourOk && trendEstablished && htf2Short && atrExpanding && closeShortOk;
   if(!longSignal && !shortSignal) return;

   int direction = longSignal ? 1 : -1;
   double price = (direction==1) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = NormalizeDouble(price - direction*InpAtrSlMult*atr1, _Digits);
   double tp = NormalizeDouble(price + direction*InpAtrTpMult*atr1, _Digits);

   double lots = GetLots(price, sl);
   if(lots<=0) return;

   bool ok = (direction==1) ? trade.Buy(lots, _Symbol, 0.0, sl, tp, "GoldBot long")
                            : trade.Sell(lots, _Symbol, 0.0, sl, tp, "GoldBot short");
   if(!ok)
      PrintFormat("entry rejected: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Act once per newly closed M5 bar - not every tick.                |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime curBarTime = iTime(_Symbol, PERIOD_M5, 0);
   if(curBarTime == g_lastBarTime) return;
   if(!LoadM5()) return;                         // not enough history yet: try again next tick
   g_lastBarTime = curBarTime;

   ManageTrailingStop();
   CheckEntrySignal();
}
//+------------------------------------------------------------------+
