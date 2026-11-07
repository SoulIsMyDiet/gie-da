cmake_minimum_required(VERSION 4.3)
project(gieda)

set(CMAKE_CXX_STANDARD 20)

add_executable(gieda WIN32
    main.cpp
    StockMarket/MainWindow/MainWindow.cpp
    StockMarket/ForexPriceMonitor.cpp
    StockMarket/MainWindow/MainWindowDisplay.cpp
    StockMarket/StockMarketClient/TwelveDataClient.cpp
    StockMarket/StockMarketClient/PriceHistoryClient.cpp
    Ai/AiClient/DailySentenceClient.cpp
    Utility/AudioSignal.cpp
)
set_property(TARGET gieda PROPERTY CXX_SCAN_FOR_MODULES OFF)
target_include_directories(gieda PRIVATE
    Ai/AiClient
    StockMarket
    StockMarket/MainWindow
    StockMarket/StockMarketClient
    Utility
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
