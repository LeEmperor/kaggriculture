#include "kaggriculture/model.hpp"

#include <stdexcept>

namespace kaggriculture {
namespace {

[[nodiscard]] constexpr std::size_t tile_index(const std::size_t x,
                                                const std::size_t y) noexcept {
  return y * kDefaultBoardSize + x;
}

}  // namespace

GameState initial_state(const GameConfig& config) {
  if (config.board_size != kDefaultBoardSize) {
    throw std::invalid_argument(
        "the initial scalar slice currently supports only boardSize=10");
  }
  if (config.episode_steps < 2) {
    throw std::invalid_argument("episodeSteps must be at least 2");
  }
  if (config.turns_per_day == 0) {
    throw std::invalid_argument("turnsPerDay must be positive");
  }

  GameState state{};
  state.config = config;
  for (auto& farm : state.farms) {
    farm.money = static_cast<double>(config.starting_money);
    farm.farmer = Position{4, 4};
    for (std::size_t y = 0; y < kDefaultBoardSize; ++y) {
      for (std::size_t x = 0; x < kDefaultBoardSize; ++x) {
        farm.tiles[tile_index(x, y)] =
            (x < 5 && y < 5) ? TileKind::empty : TileKind::locked;
      }
    }
  }
  return state;
}

void step(GameState& state, const JointAction& actions) {
  if (state.status == Status::done) {
    throw std::logic_error("cannot step a completed game");
  }

  // PASS is the only operation in this first verified rule slice.
  for (const auto& action : actions) {
    if (action.farmer != UnitOperation::pass) {
      throw std::invalid_argument("operation is not implemented in the scalar slice");
    }
  }

  ++state.transitions;
  state.day = static_cast<std::uint16_t>(state.transitions / state.config.turns_per_day);
  state.hour = static_cast<std::uint16_t>(state.transitions % state.config.turns_per_day);

  // The interpreter sees prior observation steps 0..episodeSteps-2. It marks
  // DONE while processing episodeSteps-2, producing episodeSteps-1 transitions.
  if (state.transitions >= state.config.episode_steps - 1) {
    state.status = Status::done;
  }
}

Observation observe(const GameState& state, const std::uint8_t player) {
  if (player >= kPlayerCount) {
    throw std::out_of_range("player must be 0 or 1");
  }
  return Observation{player, state.transitions, state.day, state.hour, state.status,
                     state.farms};
}

GameResult run_game(const GameConfig& config, const PassPolicy& a,
                    const PassPolicy& b) {
  GameState state = initial_state(config);
  while (state.status == Status::active) {
    const JointAction actions{a.act(observe(state, 0)), b.act(observe(state, 1))};
    step(state, actions);
  }
  return GameResult{{state.farms[0].money, state.farms[1].money},
                    state.transitions};
}

}  // namespace kaggriculture

