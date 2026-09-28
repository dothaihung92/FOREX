//+------------------------------------------------------------------+
//| GoldBot_DynamicTP_Fixed.mq5                                       |
//|                                                                    |
//| Repaired rewrite of GoldBot_DynamicTP_FastClose.mq5.               |
//| Same idea - EMA-direction entry, grid additions, basket-level      |
//| dynamic take-profit - with the ten defects found in the original   |
//| fixed and the risk bounded.                                        |
//|                                                                    |
//| ================= WHAT CHANGED AND WHY =========================== |
//|                                                                    |
//| 1. DEADLOCK REMOVED. The original set isClosingBasket=true, fired  |
//|    OrderSendAsync, and cleared the flag only once the basket hit   |
//|    zero. A single rejected close froze the EA forever with open,   |
//|    unprotected positions. This version closes synchronously via    |
//|    CTrade, retries, and if it still cannot flatten it raises a     |
//|    loud alert instead of going silent.                             |
//|                                                                    |
//| 2. type_filling IS SET. The original built a raw MqlTradeRequest   |
//|    without type_filling, which defaults to FOK - commonly rejected |
//|    for metals (retcode 10030). That rejection is what triggered    |
//|    defect 1. CTrade negotiates the symbol's filling mode for us.   |
//|                                                                    |
//| 3. LOT MATHS FIXED. MathFloor(rawLot/lotStep)*lotStep is wrong in  |
//|    IEEE 754: 0.07/0.01 = 6.9999999999999991, so the original       |
//|    silently opened 0.06 instead of 0.07 (and 0.09 instead of 0.10).|
//|    Verified against the real backtest. MathRound is used instead.  |
//|                                                                    |
//| 4. EMPTY SIDE NO LONGER OPENS INSTANTLY. `lastPrice == 0 || dist   |
//|    >= step` short-circuited whenever a side was flat, skipping the |
//|    distance rule entirely. Now a flat side uses the basket's own   |
//|    reference price, so the spacing always applies.                 |
//|                                                                    |
//| 5. DIRECTIONAL SPACING. MathAbs meant a $1 move AGAINST the        |
//|    winning side also triggered another add on it. Now adds only    |
//|    happen in the intended direction.                               |
//|                                                                    |
//| 6. TRADE RESULTS ARE CHECKED, with a cooldown, so a rejected order |
//|    is logged once rather than retried on every tick forever.       |
//|                                                                    |
//| 7. TREND NO LONGER REPAINTS. The original read buffer index 0, the |
//|    still-forming bar, so the entry direction could flip mid-bar.   |
//|    This reads index 1, the last closed bar.                        |
//|                                                                    |
//| 8. NO PER-TICK HISTORY SCANS. HistorySelectByPosition ran once per |
//|    position in two separate functions - up to 40 selects per tick. |
//|    Commission is now read once when a position is first seen and   |
//|    cached.                                                         |
//|                                                                    |
//| 9. MARKET ORDERS SENT AT MARKET. Passing an explicit price invites |
//|    requotes on market-execution accounts; 0.0 lets the terminal    |
//|    fill.                                                           |
//|                                                                    |
//| 10. RISK IS BOUNDED. A basket loss cap and a free-margin check are |
//|    enforced. InpMaxLossPerLotUSD defaults ON. Setting it to 0      |
//|    restores the original's unlimited downside - the configuration  |
//|    that killed a $500 account in 5.2 hours in backtesting.         |
//|                                                                    |
//| ================= WHAT THE BACKTEST SAYS ========================= |
//| Five years of real XAUUSD M5, Exness Standard costs (spread $0.25, |
//| slippage $0.05/side). Original settings - the +0.01-per-layer lot  |
//| ramp - were WIPED OUT at every capital level from $500 to          |
//| $100,000. On $500 the EA won 13 baskets in a row (+$138.60) then   |
//| lost $874.88 in one: dead in 5.2 hours.                            |
//|                                                                    |
//| With a FLAT 0.01 lot (InpLotStep = 0), by starting capital:        |
//|                                                                    |
//|   config                        $500      $1,000    $5,000         |
//|   20 layers, no cap             WIPED 94d WIPED 98d  +20%          |
//|   20 layers, cap $50            WIPED  6d WIPED 13d  -62%          |
//|   20 layers, cap $100 (DEFAULT)  +29%      +14%       +3%          |
//|   10 layers, no cap              +23%      +11%       +2%          |
//|   10 layers, cap $50              -4%       -2%       -0%          |
//|   20 layers step $3, no cap      +34%      +17%       +3%          |
//|   20 layers step $3, cap $100   WIPED 63d  -50%      -10%          |
//|                                                                    |
//| The defaults here are the only row that stayed positive at all     |
//| three capital levels, and it also had the smallest worst floating  |
//| loss (-$113).                                                      |
//|                                                                    |
//| TWO FINDINGS THAT ARE NOT INTUITIVE:                               |
//|                                                                    |
//| a) A TIGHTER LOSS CAP IS WORSE. Halving the cap from $100 to $50   |
//|    turned +29% into a wipe-out in 6 days. Too tight a cap keeps    |
//|    realising losses on noise before the grid can recover - death   |
//|    by a thousand cuts. This is why the cap scales with lots held   |
//|    rather than with your account: it has to be wide enough for the |
//|    grid it is protecting.                                          |
//|                                                                    |
//| b) THE PARAMETERS ARE NOT INDEPENDENT. Step $1 + cap $100 returned |
//|    +29% on $500; step $3 + the same cap was WIPED OUT in 63 days.  |
//|    Neighbouring settings flip between healthy and fatal. Treat any |
//|    change to step, layers or cap as a change to the whole system   |
//|    and re-test it - do not tune one knob in isolation.             |
//|                                                                    |
//| That fragility is itself the most important result. These numbers  |
//| come from ONE five-year path with no out-of-sample split. A config |
//| whose neighbours blow up is not robust; it is a knife edge that    |
//| happened to land well. Do not read +29% as an expectation.         |
//|                                                                    |
//| Note also that the worst floating loss is roughly the same in      |
//| DOLLARS at every account size (-$113 to -$274), so a small account |
//| carries a far larger percentage risk from the identical basket.    |
//|                                                                    |
//| This is still a grid. It has no per-trade stop, it holds losers,   |
//| and a sustained one-way move will hurt it. Bounded is not safe.    |
//|                                                                    |
//| NOT COMPILED IN METATRADER - no Windows/MT5 in the authoring       |
//| environment. Run it in Strategy Tester, then on a demo account,    |
//| before it goes anywhere near real money.                           |
//+------------------------------------------------------------------+
#property copyright "gold_bot project"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>

input group "=== TREND ==="
input int      InpEmaFast            = 50;
input int      InpEmaSlow            = 100;

input group "=== GRID ==="
input double   InpInitialLot         = 0.01;   // first position
// DEFAULT 0.0 ON PURPOSE. The +0.01-per-layer ramp is what turned a
// losing basket into a fatal one in backtesting. Read the header.
input double   InpLotStep            = 0.0;    // added per layer (0 = flat lot)
input double   InpStepPrice          = 1.0;    // dollars between additions
input int      InpTriggerPosCount    = 6;      // switch to focus mode at this count
input int      InpMaxLayers          = 20;     // hard cap on basket size

input group "=== DYNAMIC TAKE PROFIT ==="
input double   InpBaseTargetUSD      = 2.0;    // floor for the basket target
input double   InpProfitPerLotUSD    = 10.0;   // target scales with total lots

input group "=== RISK (do not disable without reading the header) ==="
// The loss cap scales with BASKET SIZE, not with the account. That is not
// an arbitrary choice - see the header. At the default 20 layers x 0.01
// lot this works out to 0.20 x 500 = $100, the only cap that stayed
// positive at $500, $1,000 and $5,000 in backtesting. Halving it to $50
// WIPED OUT the $500 and $1,000 accounts.
input double   InpMaxLossPerLotUSD   = 500.0;  // basket loss cap per lot held (0 = no cap)
input double   InpMinFreeMarginUsd   = 50.0;   // refuse to add below this free margin
input double   InpMaxSpreadUsd       = 0.60;   // skip additions when the spread blows out

input group "=== EXECUTION ==="
input ulong    InpMaxDeviation       = 200;    // slippage tolerance, points
input int      InpCloseRetries       = 5;      // attempts to flatten a basket
input int      InpFailCooldownSec    = 30;     // pause after a rejected order
input long     InpMagic              = 20260916;

input group "=== DISPLAY ==="
input bool     InpShowLines          = true;
input bool     InpShowDashboard      = true;

CTrade   trade;
int      handleEmaFast = INVALID_HANDLE;
int      handleEmaSlow = INVALID_HANDLE;
datetime g_cooldownUntil = 0;
bool     g_flattenFailed = false;

// Commission cache: read once per position instead of scanning history
// on every tick (defect 8).
ulong    g_commTicket[];
double   g_commValue[];

//+------------------------------------------------------------------+
struct BasketInfo
{
   int      count;
   double   volume;
   double   profit;
   double   buyProfit;
   double   sellProfit;
   int      buyCount;
   int      sellCount;
   double   lastBuyPrice;
   double   lastSellPrice;
   double   highestPrice;
   double   lowestPrice;
   double   firstPrice;      // reference for a side that is still flat
};

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpEmaFast <= 0 || InpEmaSlow <= 0 || InpStepPrice <= 0.0 ||
      InpInitialLot <= 0.0 || InpMaxLayers < 1)
   {
      Print("Invalid inputs - check EMA periods, step, lot and layer cap.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpLotStep > 0.0)
      Print("WARNING: InpLotStep > 0 re-enables the lot ramp. Backtesting ",
            "wiped out every capital level tested with it on. Read the header.");
   if(InpMaxLossPerLotUSD <= 0.0)
      Print("WARNING: InpMaxLossPerLotUSD = 0 means this basket has NO loss ",
            "limit. Backtesting wiped out the $500 and $1,000 accounts that way.");
   double worstCase = InpMaxLayers * InpInitialLot * InpMaxLossPerLotUSD;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal > 0.0 && worstCase > 0.0)
   {
      PrintFormat("Basket loss cap at full size: $%.2f = %.1f%% of balance.",
                  worstCase, 100.0 * worstCase / bal);
      if(worstCase > 0.25 * bal)
         Print("WARNING: one capped basket can cost more than a quarter of ",
               "this account. Backtested worst floating losses were -$113 to ",
               "-$274 in absolute terms regardless of account size, so a small ",
               "account carries a far larger percentage risk.");
   }

   handleEmaFast = iMA(_Symbol, _Period, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   handleEmaSlow = iMA(_Symbol, _Period, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   if(handleEmaFast == INVALID_HANDLE || handleEmaSlow == INVALID_HANDLE)
   {
      Print("EMA handle creation failed: ", GetLastError());
      return(INIT_FAILED);
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints((ulong)InpMaxDeviation);
   // Defect 2: let CTrade negotiate a filling mode the symbol accepts,
   // instead of a raw request that silently defaults to FOK.
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   ArrayResize(g_commTicket, 0);
   ArrayResize(g_commValue, 0);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(handleEmaFast != INVALID_HANDLE) IndicatorRelease(handleEmaFast);
   if(handleEmaSlow != INVALID_HANDLE) IndicatorRelease(handleEmaSlow);
   ObjectsDeleteAll(0, "GoldBot_");
   Comment("");
}

//+------------------------------------------------------------------+
//| Commission for a position, read from history once and cached.     |
//+------------------------------------------------------------------+
double CachedCommission(const ulong ticket, const ulong positionId)
{
   int n = ArraySize(g_commTicket);
   for(int i = 0; i < n; i++)
      if(g_commTicket[i] == ticket)
         return g_commValue[i];

   double comm = 0.0;
   if(HistorySelectByPosition(positionId))
   {
      int deals = HistoryDealsTotal();
      for(int j = 0; j < deals; j++)
      {
         ulong dealTicket = HistoryDealGetTicket(j);
         if(dealTicket > 0)
            comm += HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      }
   }
   ArrayResize(g_commTicket, n + 1);
   ArrayResize(g_commValue, n + 1);
   g_commTicket[n] = ticket;
   g_commValue[n]  = comm;
   return comm;
}

void ForgetCommissionCache()
{
   ArrayResize(g_commTicket, 0);
   ArrayResize(g_commValue, 0);
}

//+------------------------------------------------------------------+
//| One pass over our positions. Replaces the original's two separate |
//| scans, each of which hit history per position, per tick.          |
//+------------------------------------------------------------------+
BasketInfo ReadBasket()
{
   BasketInfo b;
   b.count = 0; b.volume = 0.0; b.profit = 0.0;
   b.buyProfit = 0.0; b.sellProfit = 0.0;
   b.buyCount = 0; b.sellCount = 0;
   b.lastBuyPrice = 0.0; b.lastSellPrice = 0.0;
   b.highestPrice = 0.0; b.lowestPrice = 0.0; b.firstPrice = 0.0;

   datetime lastBuyTime = 0, lastSellTime = 0, firstTime = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)          continue;
      if(PositionGetInteger(POSITION_MAGIC)  != InpMagic)        continue;

      long     type      = PositionGetInteger(POSITION_TYPE);
      double   openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      datetime openTime  = (datetime)PositionGetInteger(POSITION_TIME);
      double   volume    = PositionGetDouble(POSITION_VOLUME);
      ulong    posId     = (ulong)PositionGetInteger(POSITION_IDENTIFIER);

      double profit = PositionGetDouble(POSITION_PROFIT)
                    + PositionGetDouble(POSITION_SWAP)
                    + CachedCommission(ticket, posId);

      b.count++;
      b.volume += volume;
      b.profit += profit;

      if(b.highestPrice == 0.0 || openPrice > b.highestPrice) b.highestPrice = openPrice;
      if(b.lowestPrice  == 0.0 || openPrice < b.lowestPrice)  b.lowestPrice  = openPrice;
      if(firstTime == 0 || openTime < firstTime) { firstTime = openTime; b.firstPrice = openPrice; }

      if(type == POSITION_TYPE_BUY)
      {
         b.buyCount++;
         b.buyProfit += profit;
         if(openTime >= lastBuyTime) { lastBuyTime = openTime; b.lastBuyPrice = openPrice; }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         b.sellCount++;
         b.sellProfit += profit;
         if(openTime >= lastSellTime) { lastSellTime = openTime; b.lastSellPrice = openPrice; }
      }
   }
   return b;
}

//+------------------------------------------------------------------+
//| The basket loss limit in money, derived from how much is actually |
//| at risk (lots held) rather than from the account balance.         |
//+------------------------------------------------------------------+
double BasketLossLimitUsd(const double totalLots)
{
   if(InpMaxLossPerLotUSD <= 0.0 || totalLots <= 0.0) return 0.0;
   return totalLots * InpMaxLossPerLotUSD;
}

//+------------------------------------------------------------------+
double RequiredTP(const double totalLots)
{
   return MathMax(InpBaseTargetUSD, totalLots * InpProfitPerLotUSD);
}

//+------------------------------------------------------------------+
//| Defect 3: MathRound, not MathFloor. The original lost a step to   |
//| floating point at layers 7 and 10.                                |
//+------------------------------------------------------------------+
double NextLot(const int positionCount)
{
   double raw     = InpInitialLot + positionCount * InpLotStep;
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0.0) lotStep = 0.01;

   double lots = MathRound(raw / lotStep) * lotStep;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
double CurrentSpread()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
}

bool InCooldown()
{
   return (g_cooldownUntil > 0 && TimeCurrent() < g_cooldownUntil);
}

//+------------------------------------------------------------------+
//| Defect 6: check the result, log once, and back off - rather than  |
//| retrying a rejected order on every tick for the rest of the day.  |
//+------------------------------------------------------------------+
bool TryOpen(const bool isBuy, const double lots, const string tag)
{
   if(InCooldown()) return false;

   if(AccountInfoDouble(ACCOUNT_MARGIN_FREE) < InpMinFreeMarginUsd)
   {
      Print("Skipping ", tag, ": free margin ",
            DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2),
            " below InpMinFreeMarginUsd.");
      g_cooldownUntil = TimeCurrent() + InpFailCooldownSec;
      return false;
   }
   if(InpMaxSpreadUsd > 0.0 && CurrentSpread() > InpMaxSpreadUsd)
      return false;   // transient; no cooldown, just wait it out

   // Defect 9: price 0.0 = fill at market.
   bool ok = isBuy ? trade.Buy(lots, _Symbol, 0.0, 0.0, 0.0, tag)
                   : trade.Sell(lots, _Symbol, 0.0, 0.0, 0.0, tag);
   if(!ok)
   {
      PrintFormat("%s rejected: %d %s - pausing %ds",
                  tag, trade.ResultRetcode(),
                  trade.ResultRetcodeDescription(), InpFailCooldownSec);
      g_cooldownUntil = TimeCurrent() + InpFailCooldownSec;
      return false;
   }
   ForgetCommissionCache();
   return true;
}

//+------------------------------------------------------------------+
//| Defect 1 + 2: flatten synchronously, retry, and shout if it fails |
//| instead of freezing with unprotected positions.                   |
//+------------------------------------------------------------------+
bool FlattenBasket(const string why)
{
   for(int attempt = 1; attempt <= InpCloseRetries; attempt++)
   {
      bool allClosed = true;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol)   continue;
         if(PositionGetInteger(POSITION_MAGIC)  != InpMagic) continue;

         if(!trade.PositionClose(ticket, (ulong)InpMaxDeviation))
         {
            allClosed = false;
            PrintFormat("Close failed #%I64u (attempt %d/%d): %d %s",
                        ticket, attempt, InpCloseRetries,
                        trade.ResultRetcode(), trade.ResultRetcodeDescription());
         }
      }
      if(allClosed)
      {
         Print("Basket flattened (", why, ").");
         ForgetCommissionCache();
         g_flattenFailed = false;
         return true;
      }
      Sleep(300);
   }

   // The original would have silently frozen here. Be loud instead: the
   // positions are still open and still unprotected.
   if(!g_flattenFailed)
   {
      g_flattenFailed = true;
      string msg = "GoldBot: COULD NOT FLATTEN BASKET (" + why +
                   "). Positions are still open with no stop. Intervene now.";
      Print(msg);
      Alert(msg);
      SendNotification(msg);
   }
   return false;
}

//+------------------------------------------------------------------+
//| Defect 7: read the last CLOSED bar, so the direction cannot flip  |
//| back and forth inside the forming bar.                            |
//+------------------------------------------------------------------+
int TrendDirection()
{
   double fast[2], slow[2];
   if(CopyBuffer(handleEmaFast, 0, 1, 1, fast) <= 0) return 0;
   if(CopyBuffer(handleEmaSlow, 0, 1, 1, slow) <= 0) return 0;
   if(fast[0] > slow[0]) return  1;
   if(fast[0] < slow[0]) return -1;
   return 0;
}

//+------------------------------------------------------------------+
void ManageGrid(const BasketInfo &b)
{
   if(b.count >= InpMaxLayers) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lots = NextLot(b.count);
   string suffix = "L" + IntegerToString(b.count + 1);

   if(b.count >= InpTriggerPosCount)
   {
      // Focus mode: add to whichever side is carrying the profit.
      bool favourBuy = (b.buyProfit >= b.sellProfit);

      // Defect 4: a flat side has lastPrice == 0. The original treated
      // that as "open immediately". Fall back to the basket's first
      // entry so the spacing rule always has a reference.
      double reference = favourBuy ? b.lastBuyPrice : b.lastSellPrice;
      if(reference == 0.0) reference = b.firstPrice;
      if(reference == 0.0) return;

      // Defect 5: directional, not MathAbs. Only add when price has moved
      // a full step in the direction that side wants.
      if(favourBuy)
      {
         if(ask >= reference + InpStepPrice)
            TryOpen(true, lots, "Focus BUY " + suffix);
      }
      else
      {
         if(bid <= reference - InpStepPrice)
            TryOpen(false, lots, "Focus SELL " + suffix);
      }
      return;
   }

   // Breakout phase: extend above the highest entry or below the lowest.
   if(b.highestPrice > 0.0 && ask >= b.highestPrice + InpStepPrice)
   {
      TryOpen(true, lots, "Grid BUY " + suffix);
      return;
   }
   if(b.lowestPrice > 0.0 && bid <= b.lowestPrice - InpStepPrice)
      TryOpen(false, lots, "Grid SELL " + suffix);
}

//+------------------------------------------------------------------+
void DrawLines(const BasketInfo &b)
{
   if(!InpShowLines || b.count == 0 || b.count >= InpMaxLayers)
   {
      ObjectDelete(0, "GoldBot_T1");
      ObjectDelete(0, "GoldBot_T2");
      return;
   }
   if(b.count >= InpTriggerPosCount)
   {
      bool favourBuy = (b.buyProfit >= b.sellProfit);
      double reference = favourBuy ? b.lastBuyPrice : b.lastSellPrice;
      if(reference == 0.0) reference = b.firstPrice;
      if(reference == 0.0) return;
      if(favourBuy)
         MoveLine("GoldBot_T1", reference + InpStepPrice, clrDodgerBlue, "Next BUY");
      else
         MoveLine("GoldBot_T2", reference - InpStepPrice, clrCrimson, "Next SELL");
   }
   else
   {
      if(b.highestPrice > 0.0)
         MoveLine("GoldBot_T1", b.highestPrice + InpStepPrice, clrDodgerBlue, "Next BUY");
      if(b.lowestPrice > 0.0)
         MoveLine("GoldBot_T2", b.lowestPrice - InpStepPrice, clrCrimson, "Next SELL");
   }
}

void MoveLine(const string name, const double price, const color clr, const string text)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetString(0, name, OBJPROP_TEXT, text);
   }
   else
      ObjectMove(0, name, 0, 0, price);
}

//+------------------------------------------------------------------+
void Dashboard(const BasketInfo &b)
{
   if(!InpShowDashboard) { Comment(""); return; }

   double target = RequiredTP(b.volume);
   double cap = BasketLossLimitUsd(b.volume);
   string risk = (cap > 0.0)
                 ? "-$" + DoubleToString(cap, 2)
                 : "NONE - unlimited downside";

   string s = "GOLDBOT - DynamicTP (fixed)\n";
   s += "-----------------------------------\n";
   s += "Symbol      : " + _Symbol + "\n";
   s += "Positions   : " + IntegerToString(b.count) + " / " + IntegerToString(InpMaxLayers) + "\n";
   s += "Volume      : " + DoubleToString(b.volume, 2) + " lots\n";
   s += "Basket P/L  : " + DoubleToString(b.profit, 2) + " USD\n";
   s += "Target      : " + DoubleToString(target, 2) + " USD\n";
   s += "Loss limit  : " + risk + "\n";
   s += "BUY  " + IntegerToString(b.buyCount) + " / " + DoubleToString(b.buyProfit, 2) + "   ";
   s += "SELL " + IntegerToString(b.sellCount) + " / " + DoubleToString(b.sellProfit, 2) + "\n";
   if(InpLotStep > 0.0)
      s += "!! LOT RAMP ON - see file header\n";
   if(g_flattenFailed)
      s += "!! FLATTEN FAILED - POSITIONS UNPROTECTED\n";
   if(InCooldown())
      s += "cooldown until " + TimeToString(g_cooldownUntil, TIME_SECONDS) + "\n";
   Comment(s);
}

//+------------------------------------------------------------------+
void OnTick()
{
   BasketInfo b = ReadBasket();

   // A failed flatten is retried every tick rather than freezing.
   if(g_flattenFailed && b.count > 0)
   {
      if(FlattenBasket("retry after failure"))
         b = ReadBasket();
      else
      {
         Dashboard(b);
         return;
      }
   }

   if(b.count > 0)
   {
      if(b.profit >= RequiredTP(b.volume))
      {
         PrintFormat("Target reached: %.2f >= %.2f", b.profit, RequiredTP(b.volume));
         if(FlattenBasket("take profit")) b = ReadBasket();
         Dashboard(b);
         return;
      }
      double lossLimit = BasketLossLimitUsd(b.volume);
      if(lossLimit > 0.0 && b.profit <= -lossLimit)
      {
         PrintFormat("Loss limit hit: %.2f <= -%.2f", b.profit, lossLimit);
         if(FlattenBasket("loss limit")) b = ReadBasket();
         Dashboard(b);
         return;
      }
   }

   if(b.count == 0)
   {
      int trend = TrendDirection();
      if(trend == 1)       TryOpen(true,  NextLot(0), "Start BUY");
      else if(trend == -1) TryOpen(false, NextLot(0), "Start SELL");
      b = ReadBasket();
   }
   else
      ManageGrid(b);

   DrawLines(b);
   Dashboard(b);
}
//+------------------------------------------------------------------+
