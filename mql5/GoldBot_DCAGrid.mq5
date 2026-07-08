//+------------------------------------------------------------------+
//| GoldBot_DCAGrid.mq5                                               |
//| XAUUSD M5 trend-pullback EA, DCA/grid position management -       |
//| ported from gold_bot/backtester.py's run_backtest_dca_grid().      |
//|                                                                    |
//| Entry: identical trend-pullback signal as GoldBot_FixedCapitalRisk |
//| (EMA trend filter, RSI shallow-pullback trigger, MACD confirm,     |
//| session/hour filters, trend-strength filter).                      |
//|                                                                    |
//| Position management (THE PART THAT'S DIFFERENT): no ATR stop-loss  |
//| or take-profit at all. Instead: hold through sideways/adverse      |
//| moves, add another same-direction leg every InpDcaStepPrice        |
//| adverse move (up to InpDcaMaxLegs), and close the WHOLE grid only   |
//| when either (a) the trend flips against it (M5 close vs EMA-slow + |
//| M15 + H1 trend all disagreeing), or (b) the grid's total floating  |
//| loss breaches InpDcaHardStopPct of the FIXED InpBaseEquity -        |
//| whichever comes first.                                             |
//|                                                                    |
//| InpDcaHardStopPct IS NOT OPTIONAL. Backtested on real 5-year        |
//| XAUUSD M5 data: without any hard stop, the worst historical open    |
//| loss reached -65.6% of a $1000 account before the trend-reversal    |
//| exit eventually fired and recovered. That recovery is one           |
//| historical path, not a guarantee - a future adverse move that       |
//| takes longer to reverse has no floor without the hard stop. This    |
//| is the same category of risk (unbounded loss) as the equity_step    |
//| compounding blowup documented in the project README, just from a    |
//| different mechanism (no stop instead of growing lot size). See      |
//| README "DCA/grid mode" for the full backtest comparison             |
//| (with vs. without hard stop) before running this live.              |
//|                                                                    |
//| IMPORTANT: this file was written and reasoned through carefully to |
//| match the already-validated Python backtest logic, but it has NOT  |
//| been compiled or run in MetaTrader (no Windows/MT5 available in    |
//| the authoring environment). Run it in Strategy Tester first, then  |
//| forward-test on a demo account, before ever attaching to a real     |
//| account.                                                            |
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
input ENUM_TIMEFRAMES InpHtfTimeframe  = PERIOD_M15;
input int      InpHtfEmaPeriod         = 100;
input ENUM_TIMEFRAMES InpHtf2Timeframe = PERIOD_H1;   // 3rd-timeframe trend confirmation, both for entry and grid-exit
input int      InpHtf2EmaPeriod        = 100;
input double   InpMinTrendStrengthPct  = 0.0256;
input string   InpExcludedHoursUtc     = "7,9";

input group "=== Sessions (UTC) ==="
input string   InpSession1Start        = "07:00";
input string   InpSession1End          = "11:00";
input string   InpSession2Start        = "12:30";
input string   InpSession2End          = "16:00";
input int      InpBrokerUtcOffsetHours = 0;

input group "=== DCA/grid position management (see file header - hard stop is NOT optional) ==="
input double   InpBaseEquity           = 1000.0;   // FIXED capital used for sizing - never the live account equity
input double   InpDcaStepPrice         = 2.0;      // $ adverse move that triggers adding another leg
input double   InpDcaLegRiskPct        = 2.0;      // % of InpBaseEquity risked per $InpDcaStepPrice move, per leg
input int      InpDcaMaxLegs           = 30;        // cap on simultaneous legs in one grid
input double   InpDcaHardStopPct       = 15.0;      // % of InpBaseEquity - closes the WHOLE grid if breached. DO NOT SET TO 0.
input int      InpMaxTradesPerDay      = 50;        // grid adds count as "trades" - higher cap than the main EA by design
input double   InpMaxDailyLossPct      = 15.0;      // matches hard stop scale since one grid can be the whole day's risk
input int      InpMagicNumber          = 20240503;
input int      InpSlippagePoints       = 50;

CTrade trade;

int hEmaFastM5 = INVALID_HANDLE;
int hEmaSlowM5 = INVALID_HANDLE;
int hRsiM5     = INVALID_HANDLE;
int hAtrM5     = INVALID_HANDLE;
int hMacdM5    = INVALID_HANDLE;
int hEmaHtf    = INVALID_HANDLE;
int hEmaHtf2   = INVALID_HANDLE;

datetime g_lastBarTime = 0;
int      g_excludedHours[];
int      g_direction = 0;       // 0 = flat, 1 = long grid open, -1 = short grid open
double   g_lastAddPrice = 0.0;
datetime g_dayStart = 0;
int      g_tradesToday = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   hEmaFastM5 = iMA(_Symbol, PERIOD_M5, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hEmaSlowM5 = iMA(_Symbol, PERIOD_M5, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   hRsiM5     = iRSI(_Symbol, PERIOD_M5, InpRsiPeriod, PRICE_CLOSE);
   hAtrM5     = iATR(_Symbol, PERIOD_M5, InpAtrPeriod);
   hMacdM5    = iMACD(_Symbol, PERIOD_M5, 12, 26, 9, PRICE_CLOSE);
   hEmaHtf    = iMA(_Symbol, InpHtfTimeframe, InpHtfEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   hEmaHtf2   = iMA(_Symbol, InpHtf2Timeframe, InpHtf2EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(hEmaFastM5==INVALID_HANDLE || hEmaSlowM5==INVALID_HANDLE || hRsiM5==INVALID_HANDLE ||
      hAtrM5==INVALID_HANDLE || hMacdM5==INVALID_HANDLE || hEmaHtf==INVALID_HANDLE || hEmaHtf2==INVALID_HANDLE)
   {
      Print("GoldBot_DCAGrid: failed to create an indicator handle");
      return(INIT_FAILED);
   }

   if(InpDcaHardStopPct <= 0)
   {
      Print("GoldBot_DCAGrid: InpDcaHardStopPct must be > 0 - a DCA grid with no hard stop has unbounded loss. Refusing to init.");
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
   IndicatorRelease(hEmaHtf2);
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

bool InSession(datetime tUtc)
{
   MqlDateTime dt;
   TimeToStruct(tUtc, dt);
   int curMinutes = dt.hour*60 + dt.min;
   int h1s,m1s,h1e,m1e,h2s,m2s,h2e,m2e;
   ParseHHMM(InpSession1Start,h1s,m1s); ParseHHMM(InpSession1End,h1e,m1e);
   ParseHHMM(InpSession2Start,h2s,m2s); ParseHHMM(InpSession2End,h2e,m2e);
   int s1=h1s*60+m1s, e1=h1e*60+m1e;
   int s2=h2s*60+m2s, e2=h2e*60+m2e;
   bool in1 = (s1<=e1) ? (curMinutes>=s1 && curMinutes<=e1) : (curMinutes>=s1 || curMinutes<=e1);
   bool in2 = (s2<=e2) ? (curMinutes>=s2 && curMinutes<=e2) : (curMinutes>=s2 || curMinutes<=e2);
   return in1 || in2;
}

//+------------------------------------------------------------------+
//| Leg lot size: constant, risking InpDcaLegRiskPct of the FIXED       |
//| InpBaseEquity per InpDcaStepPrice of adverse move - matches         |
//| RiskManager.dca_leg_lots() in gold_bot/risk_manager.py.             |
//+------------------------------------------------------------------+
double DcaLegLots()
{
   double contractSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(contractSize <= 0) contractSize = 100.0;
   double riskAmount = InpDcaLegRiskPct/100.0 * InpBaseEquity;
   double lossPerLot = InpDcaStepPrice * contractSize;
   if(lossPerLot <= 0) return 0.0;
   double lots = riskAmount / lossPerLot;

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(lotStep > 0) lots = MathRound(lots/lotStep) * lotStep;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   return lots;
}

//+------------------------------------------------------------------+
//| Count legs and total floating P&L of all open positions under our  |
//| magic number (there is no per-order SL/TP in this design - the     |
//| EA itself decides when to close the whole grid).                   |
//+------------------------------------------------------------------+
int CountGridLegs(double &floatingPnl)
{
   int count = 0;
   floatingPnl = 0.0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket==0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) continue;
      count++;
      floatingPnl += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   return count;
}

void CloseAllGridLegs()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket==0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) continue;
      trade.PositionClose(ticket);
   }
   g_direction = 0;
   g_lastAddPrice = 0.0;
}

void ResetDailyCounterIfNewDay()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour=0; dt.min=0; dt.sec=0;
   datetime today = StructToTime(dt);
   if(today != g_dayStart)
   {
      g_dayStart = today;
      g_tradesToday = 0;
   }
}

//+------------------------------------------------------------------+
//| Trend condition - SAME definition used for entry AND for deciding   |
//| the grid has been invalidated (M5 close vs EMA-slow, M15 and H1     |
//| both agreeing).                                                     |
//+------------------------------------------------------------------+
bool TrendOk(int direction, double closeM5, double emaSlowM5, int htfTrend, int htf2Trend)
{
   if(direction==1)
      return (closeM5 > emaSlowM5) && (htfTrend > 0) && (htf2Trend > 0);
   else
      return (closeM5 < emaSlowM5) && (htfTrend < 0) && (htf2Trend < 0);
}

int GetHtfTrend(int handle, ENUM_TIMEFRAMES tf)
{
   double htfEma[]; ArraySetAsSeries(htfEma,true);
   double htfClose[]; ArraySetAsSeries(htfClose,true);
   if(CopyBuffer(handle,0,1,2,htfEma)<2) return 0;
   if(CopyClose(_Symbol,tf,1,2,htfClose)<2) return 0;
   if(htfClose[0] > htfEma[0]) return 1;
   if(htfClose[0] < htfEma[0]) return -1;
   return 0;
}

//+------------------------------------------------------------------+
void CheckEntrySignal(double closeM5, double emaSlowM5, double emaFastM5, double atr1, int htfTrend, int htf2Trend)
{
   double rsiBuf[], macdMain[], macdSignal[];
   ArraySetAsSeries(rsiBuf,true); ArraySetAsSeries(macdMain,true); ArraySetAsSeries(macdSignal,true);
   if(CopyBuffer(hRsiM5,0,0,10,rsiBuf)<10) return;
   if(CopyBuffer(hMacdM5,0,0,10,macdMain)<10) return;
   if(CopyBuffer(hMacdM5,1,0,10,macdSignal)<10) return;

   double rsi1 = rsiBuf[1], rsi2 = rsiBuf[2];
   double macdHist1 = macdMain[1]-macdSignal[1];
   double macdHist2 = macdMain[2]-macdSignal[2];

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
   MqlDateTime dtStruct; TimeToStruct(bar1TimeUtc, dtStruct);
   bool hourOk = !IsHourExcluded(dtStruct.hour);

   double trendStrengthPct = MathAbs(emaFastM5-emaSlowM5)/closeM5*100.0;
   bool trendEstablished = trendStrengthPct > InpMinTrendStrengthPct;

   bool uptrend = TrendOk(1, closeM5, emaSlowM5, htfTrend, htf2Trend);
   bool downtrend = TrendOk(-1, closeM5, emaSlowM5, htfTrend, htf2Trend);

   bool longSignal = uptrend && recentOversold && rsiCrossUp && macdRising &&
                     (closeM5 > emaFastM5) && sessionOk && hourOk && trendEstablished;
   bool shortSignal = downtrend && recentOverbought && rsiCrossDown && macdFalling &&
                      (closeM5 < emaFastM5) && sessionOk && hourOk && trendEstablished;

   if(!longSignal && !shortSignal) return;
   if(g_tradesToday >= InpMaxTradesPerDay) return;

   int direction = longSignal ? 1 : -1;
   double lots = DcaLegLots();
   if(lots<=0) return;

   double price = (direction==1) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool ok = (direction==1) ? trade.Buy(lots, _Symbol, price, 0, 0, "GoldBot DCA leg 1")
                            : trade.Sell(lots, _Symbol, price, 0, 0, "GoldBot DCA leg 1");
   if(ok)
   {
      g_direction = direction;
      g_lastAddPrice = closeM5;
      g_tradesToday++;
   }
}

//+------------------------------------------------------------------+
void ManageGrid(double closeM5, double emaSlowM5, int htfTrend, int htf2Trend)
{
   double floatingPnl = 0.0;
   int legs = CountGridLegs(floatingPnl);
   if(legs == 0) { g_direction = 0; return; }

   double hardStopUsd = InpDcaHardStopPct/100.0 * InpBaseEquity;
   bool trendOk = TrendOk(g_direction, closeM5, emaSlowM5, htfTrend, htf2Trend);
   bool hitHardStop = floatingPnl <= -hardStopUsd;

   if(!trendOk || hitHardStop)
   {
      if(hitHardStop) Print("GoldBot_DCAGrid: HARD STOP hit, floating P&L=", floatingPnl, " - closing entire grid");
      else Print("GoldBot_DCAGrid: trend reversal detected - closing entire grid");
      CloseAllGridLegs();
      return;
   }

   if(legs < InpDcaMaxLegs)
   {
      double adverseMove = g_direction * (g_lastAddPrice - closeM5);
      if(adverseMove >= InpDcaStepPrice && g_tradesToday < InpMaxTradesPerDay)
      {
         double lots = DcaLegLots();
         if(lots > 0)
         {
            double price = (g_direction==1) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
            bool ok = (g_direction==1) ? trade.Buy(lots, _Symbol, price, 0, 0, "GoldBot DCA add")
                                       : trade.Sell(lots, _Symbol, price, 0, 0, "GoldBot DCA add");
            if(ok)
            {
               g_lastAddPrice = closeM5;
               g_tradesToday++;
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime curBarTime = iTime(_Symbol, PERIOD_M5, 0);
   if(curBarTime == g_lastBarTime) return;
   g_lastBarTime = curBarTime;

   ResetDailyCounterIfNewDay();

   double emaFast[], emaSlow[], atrBuf[], closeBuf[];
   ArraySetAsSeries(emaFast,true); ArraySetAsSeries(emaSlow,true);
   ArraySetAsSeries(atrBuf,true); ArraySetAsSeries(closeBuf,true);
   if(CopyBuffer(hEmaFastM5,0,0,3,emaFast)<3) return;
   if(CopyBuffer(hEmaSlowM5,0,0,3,emaSlow)<3) return;
   if(CopyBuffer(hAtrM5,0,0,3,atrBuf)<3) return;
   if(CopyClose(_Symbol,PERIOD_M5,0,3,closeBuf)<3) return;

   double close1 = closeBuf[1];
   double emaFast1 = emaFast[1];
   double emaSlow1 = emaSlow[1];
   double atr1 = atrBuf[1];
   if(atr1<=0) return;

   int htfTrend = GetHtfTrend(hEmaHtf, InpHtfTimeframe);
   int htf2Trend = GetHtfTrend(hEmaHtf2, InpHtf2Timeframe);

   double floatingPnl = 0.0;
   int legs = CountGridLegs(floatingPnl);
   if(legs > 0)
      ManageGrid(close1, emaSlow1, htfTrend, htf2Trend);
   else
      CheckEntrySignal(close1, emaSlow1, emaFast1, atr1, htfTrend, htf2Trend);
}
//+------------------------------------------------------------------+
