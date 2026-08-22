# How the Kaggriculture Market Game Works

Kaggriculture is a two-player farming and logistics game built around a shared,
dynamic commodity market. The objective is simple: finish the season with more
money in the bank than your opponent. Unsold goods do not count toward the
final score.

This document is an explanatory overview. The authoritative project roadmap is
[`kaggriculture_gameplan.md`](kaggriculture_gameplan.md), and the detailed game
rules are in
[`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md). When prose
is ambiguous, the pinned official Kaggle environment is the behavioral source
of truth.

## The Game at a Glance

Each player starts with:

- $3,000;
- an empty 10x10 farm;
- only the northwest 5x5 quadrant unlocked;
- one farmer;
- an empty shed and no seeds; and
- access to a shared market starting near equilibrium prices.

A season nominally lasts 30 days with 24 turns per day. Under the official
interpreter's default 720-step configuration, a terminal-step convention means
there are actually 719 action transitions after the initial observation.

On every turn, a player controls:

- one physical action for each of their workers; and
- up to 10 ordered market actions.

Worker actions operate the farm. Market actions handle purchases, sales,
hiring, and land expansion.

## The Basic Economic Loop

A straightforward crop cycle looks like this:

```text
Buy seeds -> plant -> water over several days -> harvest
          -> carry produce to the shed -> sell it -> reinvest
```

Livestock adds a longer production chain:

```text
Build coop/pasture -> buy animal -> pick it up from the shed
-> place it -> feed and care for it -> collect products/fertilizer
-> return goods to the shed -> sell
```

The challenge is coordinating these processes before the season ends while
reacting to market prices and the opponent.

## Workers and Movement

Each worker can take one physical action every turn:

- move north, south, east, or west;
- plant, water, fertilize, harvest, or dig;
- build a coop or pasture;
- feed or care for an animal;
- collect animal fertilizer;
- pick up from, drop into, or place items into the shed; or
- pass.

Workers carry their own inventories. Goods normally need to be transported
back to the shed before they can be sold.

At the end of each day:

- every worker drops their inventory into the shed;
- hired hands disappear; and
- inventory that does not fit in the shed is permanently discarded.

The shed holds only 100 non-seed items, so storage and transportation are real
constraints.

Locked land can still be walked across. Workers simply cannot farm or build on
it until its quadrant is purchased. Workers can also access the central shed
from locked shed-adjacent tiles.

## Crops

There are five crops:

| Crop | Style | First useful age | Character |
| --- | --- | ---: | --- |
| Wheat | One harvest | About 2 days | Cheap and also required as animal feed |
| Carrot | One harvest | About 2 days | Fast and moderately valuable |
| Tomato | Repeating | 8 days | Produces daily, four times |
| Strawberry | Repeating | 10 days | Produces every other day, four times |
| Melon | One harvest | 10 days | Slow, with a high-value harvest |

Important lifecycle rules include:

- Plants become weeds after missing watering for two consecutive days.
- A newly planted crop already counts as having missed one watering.
- Therefore, a crop planted and not watered that same day becomes a weed that
  night.
- Watering during the appropriate bonus window increases one-time crop yield.
- Fertilizer doubles applicable watering bonuses for three days.
- Tomatoes and strawberries repeat, but are not permanent. After four
  scheduled productions, they begin decaying.
- Mature crops eventually lose stored yield and become weeds.

Planting has a simultaneous-demand rule. If multiple workers collectively
request more seeds of a crop than the player owns, none of those plant actions
succeed.

## Animals

Animals require a matching structure:

| Animal | Structure | Product | Production interval |
| --- | --- | --- | ---: |
| Goose | Coop | Eggs | Every day after setup |
| Cow | Pasture | Milk | Every two days |
| Sheep | Pasture | Wool | Every three days |

Animals must be fed with wheat. Missing two consecutive feeding days causes an
animal to escape permanently.

`CARE` banks a bonus for the next scheduled production. Caring repeatedly
between production days can accumulate a larger payout, provided the animal is
also fed.

Every surviving animal also makes one fertilizer available each night. This
does not accumulate: if it is not collected, the animal still holds only one.

Animals can produce indefinitely, making them attractive long-term
investments, but they consume:

- upfront capital;
- construction and placement actions;
- daily feeding labor;
- wheat; and
- harvesting and transport capacity.

## The Shared Market

The market is the game's defining system.

Seeds and animals have fixed purchase prices, but product prices change with
shared market inventory:

- selling adds supply and lowers the price;
- buying products removes supply and raises the price;
- town consumption removes supply and tends to raise prices; and
- both players trade against the same market.

Only wheat and fertilizer can be bought as products. All products can be sold.

Orders from both players are interleaved one unit at a time. When both players
sell the same product, one player does not simply execute an entire large order
before the other. The exact quote order matters:

- sells are priced using inventory before the unit is added;
- buys are priced using inventory after the unit is removed; and
- buying and immediately reselling against an otherwise unchanged market
  produces no arbitrage profit.

Some goods tolerate oversupply, while premium goods can crash rapidly to the
$1 price floor. Blindly producing the nominally most expensive product is
therefore often a poor strategy.

## Town Demand

The town continuously consumes market goods:

- the town center consumes one of nearly every product once per day;
- a new shop instance unlocks every three days;
- shops consume particular combinations of products every four turns;
- the same shop can unlock more than once; and
- each duplicate shop consumes independently.

Examples of shop demand include:

- bakeries demand eggs and wheat;
- pizza shops demand milk, tomatoes, and wheat;
- yarn stores heavily demand wool; and
- pet cafes heavily demand carrots.

This creates public demand signals. A newly unlocked shop can make a resource
increasingly scarce and valuable, but both players can see and react to it.

## Hiring and Expansion

Market actions also let players hire temporary farm hands and buy additional
5x5 land quadrants.

Hands last only for the current day. Their costs follow an escalating
Fibonacci-style schedule that resets daily:

```text
$1, $1, $2, $3, $5, $8, $13, ...
```

Additional land costs:

```text
$1,000 -> $2,000 -> $4,000
```

Labor is cheap early in the daily hiring sequence, while land expansion is a
substantial capital decision.

## Information and Competition

Both players can see:

- both farms;
- money;
- worker positions;
- crops, animals, structures, and weeds;
- unlocked land;
- market prices and inventory; and
- unlocked town shops.

A player cannot see the opponent's:

- shed contents;
- seed inventory; or
- worker-carried inventory.

Production is therefore partially observable. A player can see what the
opponent is growing, but not necessarily how much harvested stock they are
withholding before a sale.

## Where the Strategy Comes From

The major tradeoffs are:

1. **Money versus future production.** Seeds, animals, land, and structures tie
   up cash now for later returns.
2. **Yield versus time remaining.** A slow crop planted late may never repay
   its cost.
3. **Production versus labor.** Every extra tile creates watering, feeding,
   harvesting, and transport work.
4. **Production versus market impact.** A large harvest may depress its own
   selling price.
5. **Immediate sale versus stockpiling.** Waiting may improve prices, but
   inventory does not count at game end and shed space is limited.
6. **Maintenance versus expansion.** More land and animals are worthless if
   workers cannot maintain them.
7. **Your plan versus the opponent's plan.** Both players can crowd the same
   product market or race to satisfy emerging town demand.

A competent agent therefore needs four interconnected capabilities:

```text
Daily maintenance
      |
      v
Worker routing and scheduling
      |
      v
Capital allocation
      |
      v
Market timing and endgame liquidation
```

## What This Repository Is Building

The repository is not merely implementing a playable farming bot. Its primary
goal is a research platform for finding a strong Kaggle competition agent:

```text
Official Python game
-> deterministic reference traces
-> behaviorally identical C++ simulator
-> high-speed self-play and policy evaluation
-> policy search and champion selection
-> self-contained Python submission
```

Correctness comes first because small rule differences can invalidate millions
of simulated games. The current native implementation has a verified
initialization, PASS, and terminal slice; crops, animals, markets, and the rest
still need full differential validation. GPU and FPGA acceleration are optional
later research paths, not the immediate objective.

The currently verified subset of official behavior is recorded in
[`reference_semantics.md`](reference_semantics.md).
