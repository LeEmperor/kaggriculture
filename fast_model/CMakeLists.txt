add_library(kag_model src/model.cpp)
target_include_directories(kag_model PUBLIC include)
target_compile_features(kag_model PUBLIC cxx_std_20)
target_compile_options(kag_model PRIVATE -Wall -Wextra -Wpedantic -Wconversion)

if(KAG_ENABLE_SANITIZERS AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(kag_model PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(kag_model PUBLIC -fsanitize=address,undefined)
endif()

add_executable(kag-sim src/main.cpp)
target_link_libraries(kag-sim PRIVATE kag_model)
target_compile_options(kag-sim PRIVATE -Wall -Wextra -Wpedantic -Wconversion)

enable_testing()
add_executable(kag-model-tests tests/model_test.cpp)
target_link_libraries(kag-model-tests PRIVATE kag_model)
target_compile_options(kag-model-tests PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
add_test(NAME kag-model-tests COMMAND kag-model-tests)
if(KAG_ENABLE_SANITIZERS)
  # LeakSanitizer cannot attach under the managed workspace's ptrace policy.
  # AddressSanitizer and UndefinedBehaviorSanitizer remain active.
  set_tests_properties(kag-model-tests PROPERTIES ENVIRONMENT "ASAN_OPTIONS=detect_leaks=0")
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_test(
  NAME reference-oracle-tests
  COMMAND ${Python3_EXECUTABLE} -m unittest discover -s tests -p test_*.py
)
set_tests_properties(reference-oracle-tests PROPERTIES WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})
