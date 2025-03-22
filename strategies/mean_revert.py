import json
from typing import Any, List, Dict, Tuple
from prosperity3bt.datamodel import OrderDepth, TradingState, Order

class Logger:
    def __init__(self) -> None:
        self.logs = ""
    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end
    def flush(self, state: TradingState, orders: Dict[str, List[Order]], conversions: int, trader_data: str) -> None:
        summary = {"timestamp": state.timestamp, "trader_data": trader_data[:200]}
        print(json.dumps(summary))
        self.logs = ""

logger = Logger()

class Trader:
    def __init__(self) -> None:
        # Static fair values based on market levels.
        self.fair_values: Dict[str, float] = {
            'RAINFOREST_RESIN': 10000,
            'KELP': 2028.5
        }
        # Base thresholds for mean reversion.
        self.base_thresholds: Dict[str, float] = {
            'RAINFOREST_RESIN': 3,
            'KELP': 2
        }
        self.position_limits: Dict[str, int] = {
            'RAINFOREST_RESIN': 50,
            'KELP': 50
        }
        self.inventory_adjustment_factor: float = 0.1
        self.alpha: float = 0.1
        self.fair_values = self.fair_values.copy()
        # Base order sizes remain the same.
        self.base_order_sizes: Dict[str, int] = {
            'RAINFOREST_RESIN': 30,
            'KELP': 20
        }
        # Base offset for market making.
        self.base_offset: float = 2
        # Minimum spread required for posting market-making orders.
        self.min_spread: float = 2

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        conversions: int = 0
        trader_data: str = ""
        debug_log = ""
        positions = state.position

        for product in ['RAINFOREST_RESIN', 'KELP']:
            if product not in state.order_depths:
                continue

            order_depth: OrderDepth = state.order_depths[product]
            current_position = positions.get(product, 0)
            best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None
            if best_bid is None or best_ask is None:
                continue

            mid_market = (best_bid + best_ask) / 2

            # Update fair value using exponential smoothing.
            old_fv = self.fair_values[product]
            self.fair_values[product] = (1 - self.alpha) * old_fv + self.alpha * mid_market
            fair_value = self.fair_values[product]

            threshold = self.base_thresholds[product]
            quantity = 18  # Fixed quantity for mean reversion triggers

            debug_log += (f"{product}: mid={mid_market:.2f}, fv={fair_value:.2f}, pos={current_position}\n")
            orders: List[Order] = []
            pos_limit = self.position_limits[product]
            # Dynamic order sizing: full base order size if |position| < 10; scale down linearly, but not below 50%.
            base_order_size = self.base_order_sizes[product]
            if abs(current_position) < 10:
                dynamic_size = base_order_size
            else:
                scale = 1 - (abs(current_position) - 10) / (pos_limit - 10)
                dynamic_size = max(int(base_order_size * scale), int(base_order_size * 0.5))

            # Adjust fair value based on inventory.
            adjusted_fv = fair_value - current_position * self.inventory_adjustment_factor

            # Mean reversion logic.
            if mid_market > adjusted_fv + threshold and current_position > 0:
                debug_log += f"{product}: SELL at {best_bid} to unwind long\n"
                sell_qty = min(dynamic_size, current_position)
                orders.append(Order(product, best_bid, -sell_qty))
            elif mid_market < adjusted_fv - threshold and current_position < 0:
                debug_log += f"{product}: BUY at {best_ask} to cover short\n"
                buy_qty = min(dynamic_size, pos_limit - abs(current_position))
                orders.append(Order(product, best_ask, buy_qty))
            else:
                # Neutral (no strong mean reversion) condition: use market-making orders.
                spread = best_ask - best_bid
                dynamic_offset = self.base_offset
                if spread > self.min_spread:
                    dynamic_offset += (spread - self.min_spread) * 0.5
                if current_position < pos_limit:
                    buy_price = int(round(mid_market - dynamic_offset))
                    debug_log += f"{product}: MM BUY at {buy_price} size={dynamic_size}\n"
                    orders.append(Order(product, buy_price, dynamic_size))
                if current_position > -pos_limit:
                    sell_price = int(round(mid_market + dynamic_offset))
                    debug_log += f"{product}: MM SELL at {sell_price} size={dynamic_size}\n"
                    orders.append(Order(product, sell_price, -dynamic_size))

            result[product] = orders

        trader_data = debug_log
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
