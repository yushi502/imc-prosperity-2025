import json
from typing import Any, List, Dict, Tuple
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(
            self.to_json(
                [
                    self.compress_state(state, ""),
                    self.compress_orders(orders),
                    conversions,
                    "",
                    "",
                ]
            )
        )

        # We truncate state.traderData, trader_data, and self.logs to the same max. length to fit the log limit
        max_item_length = (self.max_log_length - base_length) // 3

        print(
            self.to_json(
                [
                    self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                    self.compress_orders(orders),
                    conversions,
                    self.truncate(trader_data, max_item_length),
                    self.truncate(self.logs, max_item_length),
                ]
            )
        )

        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [
            state.timestamp,
            trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings: dict[Symbol, Listing]) -> list[list[Any]]:
        compressed = []
        for listing in listings.values():
            compressed.append([listing.symbol, listing.product, listing.denomination])

        return compressed

    def compress_order_depths(self, order_depths: dict[Symbol, OrderDepth]) -> dict[Symbol, list[Any]]:
        compressed = {}
        for symbol, order_depth in order_depths.items():
            compressed[symbol] = [order_depth.buy_orders, order_depth.sell_orders]

        return compressed

    def compress_trades(self, trades: dict[Symbol, list[Trade]]) -> list[list[Any]]:
        compressed = []
        for arr in trades.values():
            for trade in arr:
                compressed.append(
                    [
                        trade.symbol,
                        trade.price,
                        trade.quantity,
                        trade.buyer,
                        trade.seller,
                        trade.timestamp,
                    ]
                )

        return compressed

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, observation in observations.conversionObservations.items():
            conversion_observations[product] = [
                observation.bidPrice,
                observation.askPrice,
                observation.transportFees,
                observation.exportTariff,
                observation.importTariff,
                observation.sugarPrice,
                observation.sunlightIndex,
            ]

        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders: dict[Symbol, list[Order]]) -> list[list[Any]]:
        compressed = []
        for arr in orders.values():
            for order in arr:
                compressed.append([order.symbol, order.price, order.quantity])

        return compressed

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value

        return value[: max_length - 3] + "..."


logger = Logger()



class Trader:
    def __init__(self) -> None:
        # Set initial fair values to market-relevant levels.
        # Dict[Symbol, float]
        self.initial_fair_values = {
            'RAINFOREST_RESIN': 10000,  # observed market levels ~10,000
            'KELP': 2028.5             # observed mid-price around 2028.5
        }
        self.spreads = {
            'RAINFOREST_RESIN': 2,
            'KELP': 5
        }
        self.position_limits = {
            'RAINFOREST_RESIN': 50,
            'KELP': 50
        }
        self.thresholds = {
            'RAINFOREST_RESIN': 10,
            'KELP': 2
        }
        
        self.inventory_adjustment_factor: float = 0.1
        self.alpha: float = 0.1
        self.fair_values = self.initial_fair_values.copy()

    def run(self, state: TradingState): # Tuple[Dict[Symbol, List[Order]], int, str]
        result: Dict = {} # Dict[Symbol, List[Order]]
        conversions: int = 0
        trader_data: str = ""
        debug_log = ""

        positions = state.position  # Current positions by product

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

            # Update the fair value estimate using exponential smoothing.
            old_fv = self.fair_values[product]
            self.fair_values[product] = (1 - self.alpha) * old_fv + self.alpha * mid_market
            fair_value = self.fair_values[product]

            threshold = self.thresholds[product]
            quantity = 18 

            debug_log += (f"{product} - mid_market: {mid_market:.2f}, "
                          f"updated fair_value: {fair_value:.2f}, position: {current_position}\n")

            orders: List[Order] = []

            # Mean reversion logic:
            if mid_market > fair_value + threshold and current_position > 0:
                debug_log += f"{product}: Market above fair value. Selling at best bid {best_bid}\n"
                sell_qty = min(quantity, current_position)
                orders.append(Order(product, best_bid, -sell_qty))
            elif mid_market < fair_value - threshold and current_position < 0:
                debug_log += f"{product}: Market below fair value. Buying at best ask {best_ask}\n"
                buy_qty = min(quantity, self.position_limits[product] - abs(current_position))
                orders.append(Order(product, best_ask, buy_qty))
            else:
                # Market-making mode: post both buy and sell orders around mid-market.
                offset = 0.5
                if current_position < self.position_limits[product]:
                    buy_price = mid_market - offset
                    debug_log += f"{product}: Placing market making BUY at {buy_price:.2f}\n"
                    orders.append(Order(product, int(round(buy_price)), quantity))
                if current_position > -self.position_limits[product]:
                    sell_price = mid_market + offset
                    debug_log += f"{product}: Placing market making SELL at {sell_price:.2f}\n"
                    orders.append(Order(product, int(round(sell_price)), -quantity))

            result[product] = orders

        trader_data = debug_log  # Pass our debug log as trader data.

        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
