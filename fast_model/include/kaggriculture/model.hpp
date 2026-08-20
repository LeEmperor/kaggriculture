#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace kaggriculture {

inline constexpr std::size_t kPlayerCount = 2;
inline constexpr std::size_t kDefaultBoardSize = 10;
inline constexpr std::size_t kDefaultTileCount =
    kDefaultBoardSize * kDefaultBoardSize;

enum class TileKind : std::uint8_t { empty, locked };
enum class UnitOperation : std::uint8_t { pass };
enum class Status : std::uint8_t { active, done };

struct Position {
  std::int16_t x{};
  std::int16_t y{};

  friend constexpr bool operator==(const Position&, const Position&) = default;
};

struct GameConfig {
  std::uint32_t episode_steps{720};
  std::uint16_t turns_per_day{24};
  std::uint16_t board_size{10};
  std::int64_t starting_money{3000};
  std::uint32_t seed{};
};

struct FarmState {
  double money{};
  Position farmer{};
  std::array<TileKind, kDefaultTileCount> tiles{};
};

struct GameState {
  GameConfig config{};
  std::array<FarmState, kPlayerCount> farms{};
  std::uint32_t transitions{};
  std::uint16_t day{};
  std::uint16_t hour{};
  Status status{Status::active};
};

struct PlayerAction {
  UnitOperation farmer{UnitOperation::pass};
};

using JointAction = std::array<PlayerAction, kPlayerCount>;

struct Observation {
  std::uint8_t player{};
  std::uint32_t transitions{};
  std::uint16_t day{};
  std::uint16_t hour{};
  Status status{Status::active};
  std::array<FarmState, kPlayerCount> farms{};
};

struct GameResult {
  std::array<double, kPlayerCount> money{};
  std::uint32_t transitions{};
};

class PassPolicy {
 public:
  [[nodiscard]] PlayerAction act(const Observation&) const noexcept { return {}; }
};

[[nodiscard]] GameState initial_state(const GameConfig& config = {});
void step(GameState& state, const JointAction& actions);
[[nodiscard]] Observation observe(const GameState& state, std::uint8_t player);
[[nodiscard]] GameResult run_game(const GameConfig& config, const PassPolicy& a,
                                  const PassPolicy& b);

}  // namespace kaggriculture

