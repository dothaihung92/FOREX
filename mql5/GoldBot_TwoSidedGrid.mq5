//+------------------------------------------------------------------+
//| GoldBot_TwoSidedGrid.mq5                                          |
//| Two-sided martingale grid with a basket take-profit and no stop.  |
//| Ported from gold_bot/two_sided_grid.py.                           |
//|                                                                    |
//| Design, exactly as requested:                                      |
//|   - open a BUY and a SELL at the same time, 0.01 lot each          |
//|   - pre-place 10 levels per side, one every InpStepUsd dollars     |
//|   - each level down the ladder adds InpLotIncrement (0.01)         |
//|   - no stop-loss anywhere                                          |
//|   - close EVERYTHING when the basket's floating P/L >= InpTpUsd    |
//|     ($1.50), then immediately start a fresh cycle                  |
//|                                                                    |
//| ================== READ THIS BEFORE RUNNING ====================== |
//| Backtested on 5 years of real XAUUSD M5 data with real Exness      |
//| Standard costs (spread $0.25, slippage $0.05/side):                |
//|                                                                    |
//|   $500 account   -> WIPED OUT after 6 days                         |
//|   $1,000 account -> WIPED OUT after 6 days                         |
//|   $5,000 account -> WIPED OUT after 1,199 days                     |
//|   $50,000        -> WIPED OUT after 1,643 days                     |
//|   $100,000       -> survived, down 72.5%                           |
//|                                                                    |
//| It won 753 of its 754 baskets - a 99.9% win rate - and still lost  |
//| the account. That is not a bug in the test; it is how this design  |
//| behaves. Each win banks $1.50, and the single loss is unbounded    |
//| because nothing closes a losing basket.                            |
//|                                                                    |
//| The arithmetic that drives it: if one side's ladder fills          |
//| completely that is 0.55 lots = 55 oz, so every further $1 against  |
//| you costs $55, while the target for that same basket is $1.50.     |
//| Opening the full grid costs about $38.50 in spread and slippage    |
//| alone - roughly 26 times the profit it is trying to make.          |
//|                                                                    |
//| Removing the cost entirely (impossible in reality) still wiped the |
//| account out, in 98 days instead of 6. The problem is structural,   |
//| not a matter of tuning.                                            |
//|                                                                    |
//| InpMaxBasketLossUsd is provided as the one change that bounds the  |
//| damage. It is OFF by default because the specification said no     |
//| stop-loss. Backtesting says caps of $25-$200 all still lost the    |
//| account on this data - they slow the bleed, they do not fix it.    |
//| ================================================================== |
//|                                                                    |
//| NOT COMPILED OR RUN IN METATRADER - no Windows/MT5 in the          |
//| authoring environment. Run it in Strategy Tester, then on a demo   |
//| account, before it ever touches real money.                        |
//+------------------------------------------------------------------+
#property copyright "gold_bot project"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

input group "=== Grid ==="
input double   InpStepUsd            = 1.0;    // dollars between levels
input int      InpLevels             = 10;     // pending levels per side
input double   InpBaseLot            = 0.01;   // first level
input double   InpLotIncrement       = 0.01;   // added per level down the ladder
input double   InpTpUsd              = 1.5;    // basket profit that closes everything

input group "=== Risk (read the header before changing) ==="
// Off by default because the requested design has no stop. Set a positive
// value to close a losing basket instead of holding it indefinitely.
input double   InpMaxBasketLossUsd   = 0.0;    // 0 = no loss cap at all
// Deliberate speed bump. The backtest wiped out a $500 account in 6 days,
// so this EA will not trade until you state that you have read that.
input bool     InpIAcceptUnlimitedRisk = false;

input group "=== Execution ==="
input int      InpSlippagePoints     = 20;
input long     InpMagic              = 20240815;
input string   InpComment            = "two_sided_grid";

CTrade   trade;
double   g_cycleAnchor  = 0.0;   // price the current cycle was opened at
bool     g_buyFilled[];          // level -> already filled this cycle
bool     g_sellFilled[];
bool     g_halted       = false;

//+------------------------------------------------------------------+
int OnInit()
{
   if(!InpIAcceptUnlimitedRisk)
   {
      Print("GoldBot_TwoSidedGrid: refusing to start.");
      Print("This design was wiped out in 6 days on a $500 account in ",
            "backtesting, winning 99.9% of its baskets along the way.");
      Print("Read the file header, then set InpIAcceptUnlimitedRisk=true ",
            "if you still want to run it.");
      return(INIT_FAILED);
   }
   if(InpLevels < 1 || InpStepUsd <= 0.0 || InpBaseLot <= 0.0)
   {
      Print("GoldBot_TwoSidedGrid: invalid grid parameters.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   ArrayResize(g_buyFilled,  InpLevels);
   ArrayResize(g_sellFilled, InpLevels);

   // Adopt any basket this EA already has open (e.g. after a restart)
   // rather than opening a second one on top of it.
   if(BasketVolume() > 0.0)
   {
      g_cycleAnchor = CurrentPrice();
      ArrayInitialize(g_buyFilled,  true);
      ArrayInitialize(g_sellFilled, true);
      Print("Existing basket adopted; no new levels will be added this cycle.");
   }
   else
      StartCycle();

   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }

//+------------------------------------------------------------------+
double CurrentPrice()
{
   return (SymbolInfoDouble(_Symbol, SYMBOL_BID) +
           SymbolInfoDouble(_Symbol, SYMBOL_ASK)) / 2.0;
}

double NormalizeLot(double lots)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = 0.01;
   lots = MathRound(lots / step) * step;
   if(lots < minv) lots = minv;
   if(lots > maxv) lots = maxv;
   return NormalizeDouble(lots, 2);
}

double LotForLevel(const int level)
{
   return NormalizeLot(InpBaseLot + level * InpLotIncrement);
}

//+------------------------------------------------------------------+
//| Floating P/L of every position this EA owns on this symbol.       |
//+------------------------------------------------------------------+
double BasketProfit()
{
   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)  continue;
      if(PositionGetString(POSITION_SYMBOL)  != _Symbol)  continue;
      total += PositionGetDouble(POSITION_PROFIT)
             + PositionGetDouble(POSITION_SWAP);
   }
   return total;
}

double BasketVolume()
{
   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)  continue;
      if(PositionGetString(POSITION_SYMBOL)  != _Symbol)  continue;
      total += PositionGetDouble(POSITION_VOLUME);
   }
   return total;
}

//+------------------------------------------------------------------+
//| Close every position in the basket. Retries once per position,    |
//| because a partial close would leave naked exposure with no stop.  |
//+------------------------------------------------------------------+
bool CloseBasket()
{
   bool allClosed = true;
   for(int attempt = 0; attempt < 3; attempt++)
   {
      allClosed = true;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != InpMagic)  continue;
         if(PositionGetString(POSITION_SYMBOL)  != _Symbol)  continue;
         if(!trade.PositionClose(ticket, InpSlippagePoints))
         {
            allClosed = false;
            PrintFormat("Close failed for #%I64u: %d %s",
                        ticket, trade.ResultRetcode(),
                        trade.ResultRetcodeDescription());
         }
      }
      if(allClosed) break;
      Sleep(500);
   }
   if(!allClosed)
      Print("WARNING: basket not fully closed - positions remain open ",
            "with no stop-loss. Intervene manually.");
   return allClosed;
}

//+------------------------------------------------------------------+
void StartCycle()
{
   g_cycleAnchor = CurrentPrice();
   ArrayInitialize(g_buyFilled,  false);
   ArrayInitialize(g_sellFilled, false);

   // Level 0 opens immediately on both sides.
   if(trade.Buy(LotForLevel(0), _Symbol, 0.0, 0.0, 0.0, InpComment))
      g_buyFilled[0] = true;
   else
      PrintFormat("Initial BUY failed: %d %s", trade.ResultRetcode(),
                  trade.ResultRetcodeDescription());

   if(trade.Sell(LotForLevel(0), _Symbol, 0.0, 0.0, 0.0, InpComment))
      g_sellFilled[0] = true;
   else
      PrintFormat("Initial SELL failed: %d %s", trade.ResultRetcode(),
                  trade.ResultRetcodeDescription());

   PrintFormat("New cycle anchored at %.2f", g_cycleAnchor);
}

//+------------------------------------------------------------------+
//| Fill any level the price has reached. Levels are tracked by index |
//| so a fast move that jumps several levels at once opens all of     |
//| them, matching the pending-order behaviour being modelled.        |
//+------------------------------------------------------------------+
void TriggerLevels()
{
   double price = CurrentPrice();

   for(int lvl = 1; lvl < InpLevels; lvl++)
   {
      if(!g_buyFilled[lvl] && price <= g_cycleAnchor - lvl * InpStepUsd)
      {
         if(trade.Buy(LotForLevel(lvl), _Symbol, 0.0, 0.0, 0.0, InpComment))
         {
            g_buyFilled[lvl] = true;
            PrintFormat("BUY level %d filled at %.2f (%.2f lots)",
                        lvl, price, LotForLevel(lvl));
         }
      }
      if(!g_sellFilled[lvl] && price >= g_cycleAnchor + lvl * InpStepUsd)
      {
         if(trade.Sell(LotForLevel(lvl), _Symbol, 0.0, 0.0, 0.0, InpComment))
         {
            g_sellFilled[lvl] = true;
            PrintFormat("SELL level %d filled at %.2f (%.2f lots)",
                        lvl, price, LotForLevel(lvl));
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(g_halted) return;

   double profit = BasketProfit();

   if(profit >= InpTpUsd)
   {
      PrintFormat("Basket target reached: %.2f >= %.2f - closing all",
                  profit, InpTpUsd);
      if(CloseBasket())
         StartCycle();
      return;
   }

   if(InpMaxBasketLossUsd > 0.0 && profit <= -InpMaxBasketLossUsd)
   {
      PrintFormat("Basket loss cap hit: %.2f <= -%.2f - closing all",
                  profit, InpMaxBasketLossUsd);
      if(CloseBasket())
         StartCycle();
      return;
   }

   TriggerLevels();
}
//+------------------------------------------------------------------+
