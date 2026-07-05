//+------------------------------------------------------------------+
//| GoldBot_LotStep_0.08.mq5                                          |
//| XAUUSD M5 trend-pullback EA, ported from the Python gold_bot      |
//| project (gold_bot/strategy.py + risk_manager.py).                 |
//|                                                                    |
//| Strategy: EMA(fast)/EMA(slow) trend filter on M5, confirmed by an  |
//| EMA(HTF) trend filter on a higher timeframe; entries only on a     |
//| shallow RSI pullback resuming in the trend direction, confirmed by |
//| MACD histogram turning back in that direction; only during the     |
//| configured London/New York session windows; only when the EMA     |
//| fast/slow gap is wide enough (trend actually established, not      |
//| right at the cross - validated on real backtests to matter more    |
//| than any other single filter); skips a configurable set of UTC     |
//| hours found to underperform (default: 9, common EU/UK data-release |
//| time).                                                              |
//|                                                                    |
//| Sizing: "equity_step" - lot starts at BaseLot at BaseEquity, adds   |
//| LotStep for every EquityStepUsd of profit, removes LotStep for      |
//| every EquityStepUsd of loss, floored at MinLot. This does NOT       |
//| normalize risk against the ATR stop distance - see the project      |
//| README for the full risk/return tradeoff tested at different        |
//| LotStep values before using this in a live account.                |
//|                                                                    |
//| IMPORTANT: this file was written and reasoned through carefully to |
//| match the already-validated Python backtest logic, but it has NOT  |
//| been compiled or run in MetaTrader (no Windows/MT5 available in    |
//| the authoring environment). Run it in Strategy Tester first, then  |
//| forward-test on a demo account, before ever attaching to a real     |
//| account - the usual rule for any new EA, doubly so for one that    |
//| hasn't been compiled yet.                                          |
//+------------------------------------------------------------------+
#property copyright "gold_bot project"
#property version   "1.00"
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
input double   InpAtrSlMult            = 2.0;
input double   InpAtrTpMult            = 2.5;
input ENUM_TIMEFRAMES InpHtfTimeframe  = PERIOD_M15;
input int      InpHtfEmaPeriod         = 100;
input double   InpMinTrendStrengthPct  = 0.0256;   // skip entries when |EMAfast-EMAslow|/close*100 is below this
input string   InpExcludedHoursUtc     = "9";      // comma-separated UTC hours to skip, e.g. "9,14"

input group "=== Sessions (UTC) ==="
input string   InpSession1Start        = "07:00";
input string   InpSession1End          = "11:00";
input string   InpSession2Start        = "12:30";
input string   InpSession2End          = "16:00";
input int      InpBrokerUtcOffsetHours = 0;        // broker server time minus UTC (check your broker!)

input group "=== Risk / equity_step sizing ==="
input double   InpBaseEquity           = 500.0;
input double   InpBaseLot              = 0.08;
input double   InpLotStep              = 0.08;
input double   InpEquityStepUsd        = 100.0;
input double   InpMinLot               = 0.08;
input bool     InpUseTrailingStop      = true;
input double   InpTrailingAtrMult      = 1.0;
input int      InpMaxTradesPerDay      = 4;
input double   InpMaxDailyLossPct      = 3.0;
input int      InpMaxConcurrentTrades  = 1;
input int      InpMagicNumber          = 20240501;
input int      InpSlippagePoints       = 50;

CTrade trade;

int hEmaFastM5 = INVALID_HANDLE;
int hEmaSlowM5 = INVALID_HANDLE;
int hRsiM5     = INVALID_HANDLE;
int hAtrM5     = INVALID_HANDLE;
int hMacdM5    = INVALID_HANDLE;
int hEmaHtf    = INVALID_HANDLE;

datetime g_lastBarTime = 0;
int      g_excludedHours[];

//+------------------------------------------------------------------+
int OnInit()
{
   hEmaFastM5 = iMA(_Symbol, PERIOD_M5, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hEmaSlowM5 = iMA(_Symbol, PERIOD_M5, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   hRsiM5     = iRSI(_Symbol, PERIOD_M5, InpRsiPeriod, PRICE_CLOSE);
   hAtrM5     = iATR(_Symbol, PERIOD_M5, InpAtrPeriod);
   hMacdM5    = iMACD(_Symbol, PERIOD_M5, 12, 26, 9, PRICE_CLOSE);
   hEmaHtf    = iMA(_Symbol, InpHtfTimeframe, InpHtfEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(hEmaFastM5==INVALID_HANDLE || hEmaSlowM5==INVALID_HANDLE || hRsiM5==INVALID_HANDLE ||
      hAtrM5==INVALID_HANDLE || hMacdM5==INVALID_HANDLE || hEmaHtf==INVALID_HANDLE)
   {
      Print("GoldBot: failed to create an indicator handle");
      return(INIT_FAILED);
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

   ParseExcludedHours();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hEmaFastM5);
   IndicatorRelease(hEmaSlowM5);
   IndicatorRelease(hRsiM5);
   IndicatorRelease(hAtrM5);
   IndicatorRelease(hMacdM5);
   IndicatorRelease(hEmaHtf);
}

//+------------------------------------------------------------------+
//| Parse "9,14" -> {9,14}                                            |
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
//| equity_step lot sizing - same math as gold_bot/risk_manager.py    |
//+------------------------------------------------------------------+
double GetLots()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double steps = (equity - InpBaseEquity) / InpEquityStepUsd;
   int stepsInt = (int)steps; // (int) truncates toward zero in MQL5, matching Python's int()
   double lots = InpBaseLot + InpLotStep*stepsInt;
   if(lots < InpMinLot) lots = InpMinLot;

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

bool CanOpenTrade()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) return false;

   if(CountOpenPositions() >= InpMaxConcurrentTrades) return false;

   double dailyPnl; int tradesToday;
   GetDailyStats(dailyPnl, tradesToday);

   if(tradesToday >= InpMaxTradesPerDay) return false;

   double maxLoss = -MathAbs(InpMaxDailyLossPct)/100.0 * equity;
   if(dailyPnl <= maxLoss) return false;

   return true;
}

//+------------------------------------------------------------------+
//| Trail the stop loss of any open position in our favor only.       |
//+------------------------------------------------------------------+
void ManageTrailingStop(double atrValue)
{
   if(!InpUseTrailingStop || atrValue<=0) return;

   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket==0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      int direction = (type==POSITION_TYPE_BUY) ? 1 : -1;
      double curPrice = (direction==1) ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double curSl = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);

      double newSl = curPrice - direction*InpTrailingAtrMult*atrValue;
      bool better = (direction==1) ? (newSl > curSl) : (newSl < curSl);
      if(better)
         trade.PositionModify(ticket, newSl, tp);
   }
}

//+------------------------------------------------------------------+
void CheckEntrySignal()
{
   if(!CanOpenTrade()) return;
   if(CountOpenPositions() > 0) return;

   double emaFast[], emaSlow[], rsiBuf[], atrBuf[], macdMain[], macdSignal[], closeBuf[];
   ArraySetAsSeries(emaFast,true); ArraySetAsSeries(emaSlow,true);
   ArraySetAsSeries(rsiBuf,true);  ArraySetAsSeries(atrBuf,true);
   ArraySetAsSeries(macdMain,true);ArraySetAsSeries(macdSignal,true);
   ArraySetAsSeries(closeBuf,true);

   if(CopyBuffer(hEmaFastM5,0,0,3,emaFast)<3) return;
   if(CopyBuffer(hEmaSlowM5,0,0,3,emaSlow)<3) return;
   if(CopyBuffer(hRsiM5,0,0,10,rsiBuf)<10) return;
   if(CopyBuffer(hAtrM5,0,0,3,atrBuf)<3) return;
   if(CopyBuffer(hMacdM5,0,0,10,macdMain)<10) return;   // MACD main line
   if(CopyBuffer(hMacdM5,1,0,10,macdSignal)<10) return; // MACD signal line
   if(CopyClose(_Symbol,PERIOD_M5,0,3,closeBuf)<3) return;

   // Index 1 = last fully closed M5 bar (index 0 may still be forming).
   double close1    = closeBuf[1];
   double emaFast1  = emaFast[1];
   double emaSlow1  = emaSlow[1];
   double rsi1      = rsiBuf[1];
   double rsi2      = rsiBuf[2];
   double atr1      = atrBuf[1];
   double macdHist1 = macdMain[1]-macdSignal[1];
   double macdHist2 = macdMain[2]-macdSignal[2];

   if(atr1<=0) return;

   // Higher-timeframe trend, using the last fully CLOSED HTF bar (shift=1)
   // so we never peek at an HTF bar still forming alongside this M5 bar.
   double htfEma[]; ArraySetAsSeries(htfEma,true);
   double htfClose[]; ArraySetAsSeries(htfClose,true);
   if(CopyBuffer(hEmaHtf,0,1,2,htfEma)<2) return;
   if(CopyClose(_Symbol,InpHtfTimeframe,1,2,htfClose)<2) return;

   int htfTrend = 0;
   if(htfClose[0] > htfEma[0]) htfTrend = 1;
   else if(htfClose[0] < htfEma[0]) htfTrend = -1;

   bool uptrend   = (close1 > emaSlow1) && (htfTrend > 0);
   bool downtrend = (close1 < emaSlow1) && (htfTrend < 0);

   // "Recent oversold/overbought": RSI touched near-extreme at any of the
   // 6 bars strictly before the signal bar (indices 2..7).
   bool recentOversold=false, recentOverbought=false;
   for(int i=2; i<=7; i++)
   {
      if(rsiBuf[i] <= InpRsiOversold+5) recentOversold=true;
      if(rsiBuf[i] >= InpRsiOverbought-5) recentOverbought=true;
   }

   bool rsiCrossUp   = (rsi1 > InpRsiPullback) && (rsi2 <= InpRsiPullback);
   bool rsiCrossDown = (rsi1 < (100-InpRsiPullback)) && (rsi2 >= (100-InpRsiPullback));

   bool macdRising  = macdHist1 > macdHist2;
   bool macdFalling = macdHist1 < macdHist2;

   datetime timeArr[]; ArraySetAsSeries(timeArr,true);
   if(CopyTime(_Symbol,PERIOD_M5,0,3,timeArr)<3) return;
   datetime bar1TimeUtc = timeArr[1] - InpBrokerUtcOffsetHours*3600;

   bool sessionOk = InSession(bar1TimeUtc);
   MqlDateTime dtStruct;
   TimeToStruct(bar1TimeUtc, dtStruct);
   bool hourOk = !IsHourExcluded(dtStruct.hour);

   double trendStrengthPct = MathAbs(emaFast1-emaSlow1)/close1*100.0;
   bool trendEstablished = trendStrengthPct > InpMinTrendStrengthPct;

   bool longSignal = uptrend && recentOversold && rsiCrossUp && macdRising &&
                     (close1 > emaFast1) && sessionOk && hourOk && trendEstablished;
   bool shortSignal = downtrend && recentOverbought && rsiCrossDown && macdFalling &&
                      (close1 < emaFast1) && sessionOk && hourOk && trendEstablished;

   if(!longSignal && !shortSignal) return;

   int direction = longSignal ? 1 : -1;
   double lots = GetLots();
   if(lots<=0) return;

   double price = (direction==1) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = price - direction*InpAtrSlMult*atr1;
   double tp = price + direction*InpAtrTpMult*atr1;

   if(direction==1)
      trade.Buy(lots, _Symbol, price, sl, tp, "GoldBot long");
   else
      trade.Sell(lots, _Symbol, price, sl, tp, "GoldBot short");
}

//+------------------------------------------------------------------+
//| Only act once per newly closed M5 bar - not every tick.           |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime curBarTime = iTime(_Symbol, PERIOD_M5, 0);
   if(curBarTime == g_lastBarTime) return;
   g_lastBarTime = curBarTime;

   double atrBuf[]; ArraySetAsSeries(atrBuf,true);
   if(CopyBuffer(hAtrM5,0,0,2,atrBuf) >= 2)
      ManageTrailingStop(atrBuf[1]);

   CheckEntrySignal();
}
//+------------------------------------------------------------------+
