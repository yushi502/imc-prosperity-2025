import json
from typing import Any, List, Dict, Tuple
from prosperity3bt.datamodel import OrderDepth, TradingState, Order

import numpy as np
import statistics
import math

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
        # For RAINFOREST_RESIN, we'll use regression to estimate fair value.
        self.history: Dict[str, List[Tuple[float, float]]] = {
            'RAINFOREST_RESIN': []
        }
        self.min_points: int = 10      # Need at least 10 data points.
        self.history_window: int = 50  # Keep last 50 ticks.

        # For KELP, we stick with a static fair value and threshold.
        self.base_fair_values = {
            'RAINFOREST_RESIN': 10000,
            'KELP': 2028.5
        }
        self.thresholds = {
            'RAINFOREST_RESIN': 10,
            'KELP': 2
        }
        self.position_limits = {
            'RAINFOREST_RESIN': 50,
            'KELP': 50
        }
        self.inventory_adjustment_factor: float = 0.1
        self.alpha: float = 0.1  # Smoothing factor.
        self.fair_values = self.base_fair_values.copy()
        self.base_order_size: int = 18
        self.offset: float = 0.5  # Market-making offset.

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        conversions: int = 0
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
            current_time = state.timestamp

            # For RAINFOREST_RESIN, update our history and run regression.
            if product == 'RAINFOREST_RESIN':
                self.history[product].append((current_time, mid_market))
                if len(self.history[product]) > self.history_window:
                    self.history[product].pop(0)
                if len(self.history[product]) >= self.min_points:
                    times = np.array([t for t, p in self.history[product]])
                    prices = np.array([p for t, p in self.history[product]])
                    # Linear regression: price = slope * time + intercept.
                    slope, intercept = np.polyfit(times, prices, 1)
                    predicted_fv = slope * (current_time + 1) + intercept
                    # Calculate residuals and set threshold to the standard deviation.
                    predicted_values = slope * times + intercept
                    residuals = prices - predicted_values
                    if len(residuals) >= 2:
                        dynamic_threshold = statistics.stdev(residuals)
                    else:
                        dynamic_threshold = self.thresholds[product]
                else:
                    predicted_fv = mid_market
                    dynamic_threshold = self.thresholds[product]
                # Blend predicted fair value with current mid-market.
                fair_value = (1 - self.alpha) * mid_market + self.alpha * predicted_fv
            else:
                # For KELP, use static values.
                fair_value = self.fair_values[product]
                dynamic_threshold = self.thresholds[product]

            # Adjust fair value for inventory.
            adjusted_fv = fair_value - current_position * self.inventory_adjustment_factor

            debug_log += (f"{product}: mid={mid_market:.2f}, fair_value={fair_value:.2f}, "
                          f"threshold={dynamic_threshold:.2f}, pos={current_position}\n")

            orders: List[Order] = []
            dynamic_size = self.base_order_size

            # Mean reversion trading.
            if mid_market > adjusted_fv + dynamic_threshold and current_position > 0:
                debug_log += f"{product}: SELL at {best_bid} to unwind long\n"
                sell_qty = min(dynamic_size, current_position)
                orders.append(Order(product, best_bid, -sell_qty))
            elif mid_market < adjusted_fv - dynamic_threshold and current_position < 0:
                debug_log += f"{product}: BUY at {best_ask} to cover short\n"
                buy_qty = min(dynamic_size, self.position_limits[product] - abs(current_position))
                orders.append(Order(product, best_ask, buy_qty))
            else:
                # Neutral condition: market-making orders.
                buy_price = int(round(mid_market - self.offset))
                sell_price = int(round(mid_market + self.offset))
                debug_log += f"{product}: MM BUY at {buy_price}, MM SELL at {sell_price}\n"
                orders.append(Order(product, buy_price, dynamic_size))
                orders.append(Order(product, sell_price, -dynamic_size))

            result[product] = orders

        trader_data = debug_log
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
