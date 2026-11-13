cmake_minimum_required(VERSION 4.3)
project(gieda)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(gieda WIN32
    src/main.cpp
    src/pages/MainWindow/MainWindow.cpp
    src/pages/reports/ReportsPage.cpp
    src/pages/reports/CandleGapsPage.cpp
    src/pages/reports/SignalsPage.cpp
    src/stockmarket/StockPriceMonitor.cpp
    src/pages/MainWindow/MainWindowDisplay.cpp
    src/stockmarket/StockMarketClient/TwelveDataClient.cpp
    src/stockmarket/StockMarketClient/CandlesDataClient.cpp
    src/stockmarket/StockMarketClient/CandleHistoryClient.cpp
    src/stockmarket/StockMarketClient/CandleBackfillClient.cpp
    src/ai/AiClient/DailySentenceClient.cpp
    src/utility/AudioSignal.cpp
    src/algorithms/Grok-45/EURUSD/SignalGenerator.cpp
)
set_property(TARGET gieda PROPERTY CXX_SCAN_FOR_MODULES OFF)
target_include_directories(gieda PRIVATE
    src/ai/AiClient
    src/stockmarket
    src/pages/MainWindow
    src/pages/reports
    src/stockmarket/StockMarketClient
    src/utility
    src/algorithms/Grok-45/EURUSD
)
target_link_libraries(gieda PRIVATE
    winmm
    winhttp
)
target_link_options(gieda PRIVATE -static-libgcc -static-libstdc++)

get_filename_component(MINGW_COMPILER_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
add_custom_command(TARGET gieda POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${MINGW_COMPILER_DIR}/libwinpthread-1.dll"
        "$<TARGET_FILE_DIR:gieda>/libwinpthread-1.dll"
)

include(CTest)
if(BUILD_TESTING)
    add_executable(grok45_signal_generator_tests
        src/algorithms/Grok-45/EURUSD/SignalGenerator.cpp
        src/algorithms/Grok-45/EURUSD/tests/SignalGeneratorTests.cpp
    )
    set_property(
        TARGET grok45_signal_generator_tests
        PROPERTY CXX_SCAN_FOR_MODULES OFF
    )
    target_include_directories(grok45_signal_generator_tests PRIVATE
        src/algorithms/Grok-45/EURUSD
    )
    target_link_options(grok45_signal_generator_tests PRIVATE
        -static-libgcc
        -static-libstdc++
    )
    add_test(
        NAME grok45_signal_generator_tests
        COMMAND grok45_signal_generator_tests
    )
endif()
