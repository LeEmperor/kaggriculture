cmake_minimum_required(VERSION 3.24)

project(kaggriculture LANGUAGES CXX)

option(KAG_ENABLE_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

include(CTest)
add_subdirectory(fast_model)
