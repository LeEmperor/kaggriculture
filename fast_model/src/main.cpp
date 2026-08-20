#include "kaggriculture/model.hpp"

#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string_view>

namespace {

[[nodiscard]] std::uint64_t parse_games(const int argc, char** argv) {
  std::uint64_t games = 100'000;
  for (int i = 2; i < argc; ++i) {
    if (std::string_view{argv[i]} == "--games" && i + 1 < argc) {
      const std::string_view text{argv[++i]};
      const auto result = std::from_chars(text.data(), text.data() + text.size(), games);
      if (result.ec != std::errc{} || result.ptr != text.data() + text.size() ||
          games == 0) {
        std::cerr << "--games expects a positive integer\n";
        std::exit(2);
      }
    } else {
      std::cerr << "unknown argument: " << argv[i] << '\n';
      std::exit(2);
    }
  }
  return games;
}

int benchmark(const int argc, char** argv) {
  const std::uint64_t games = parse_games(argc, argv);
  const kaggriculture::GameConfig config{};
  const kaggriculture::JointAction pass{};
  std::uint64_t transitions = 0;
  double checksum = 0;

  const auto started = std::chrono::steady_clock::now();
  for (std::uint64_t game = 0; game < games; ++game) {
    auto state = kaggriculture::initial_state(config);
    while (state.status == kaggriculture::Status::active) {
      kaggriculture::step(state, pass);
      ++transitions;
    }
    checksum += state.farms[game % kaggriculture::kPlayerCount].money;
  }
  const auto stopped = std::chrono::steady_clock::now();
  const double seconds = std::chrono::duration<double>(stopped - started).count();

  std::cout << "backend=cpp-scalar-pass-scaffold\n"
            << "games=" << games << '\n'
            << "transitions=" << transitions << '\n'
            << std::fixed << std::setprecision(3)
            << "seconds=" << seconds << '\n'
            << "games_per_second=" << static_cast<double>(games) / seconds << '\n'
            << "transitions_per_second="
            << static_cast<double>(transitions) / seconds << '\n'
            << "nanoseconds_per_transition="
            << seconds * 1.0e9 / static_cast<double>(transitions) << '\n'
            << "checksum=" << checksum << '\n';
  return 0;
}

void usage(const char* executable) {
  std::cerr << "usage: " << executable << " bench [--games N]\n";
}

}  // namespace

int main(const int argc, char** argv) {
  if (argc >= 2 && std::string_view{argv[1]} == "bench") {
    return benchmark(argc, argv);
  }
  usage(argv[0]);
  return 2;
}
