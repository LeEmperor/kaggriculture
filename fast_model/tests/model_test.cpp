#include "kaggriculture/model.hpp"

#include <cassert>
#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <stdexcept>

namespace {

void check(const bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAILED: " << message << '\n';
    std::exit(1);
  }
}

void initialization_matches_reference() {
  const auto state = kaggriculture::initial_state();
  check(state.status == kaggriculture::Status::active, "initial status");
  check(state.transitions == 0, "initial transition count");
  for (const auto& farm : state.farms) {
    check(farm.money == 3000.0, "initial money");
    check(farm.farmer == kaggriculture::Position{4, 4}, "initial farmer position");
    std::size_t empty = 0;
    std::size_t locked = 0;
    for (const auto tile : farm.tiles) {
      empty += tile == kaggriculture::TileKind::empty ? 1U : 0U;
      locked += tile == kaggriculture::TileKind::locked ? 1U : 0U;
    }
    check(empty == 25, "initial empty tile count");
    check(locked == 75, "initial locked tile count");
  }
}

void pass_game_matches_terminal_convention() {
  const kaggriculture::PassPolicy pass{};
  const auto result = kaggriculture::run_game({}, pass, pass);
  check(result.transitions == 719, "default terminal transition count");
  check(result.money[0] == 3000.0, "player zero pass reward");
  check(result.money[1] == 3000.0, "player one pass reward");
}

void rejected_configuration_is_explicit() {
  auto config = kaggriculture::GameConfig{};
  config.board_size = 8;
  bool threw = false;
  try {
    static_cast<void>(kaggriculture::initial_state(config));
  } catch (const std::invalid_argument&) {
    threw = true;
  }
  check(threw, "unsupported board size is rejected");
}

}  // namespace

int main() {
  initialization_matches_reference();
  pass_game_matches_terminal_convention();
  rejected_configuration_is_explicit();
}
