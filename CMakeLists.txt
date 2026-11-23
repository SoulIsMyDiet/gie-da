cmake_minimum_required(VERSION 4.3)
project(gieda)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(ENABLE_IBKR_TWS_API "Dołącz oficjalne C++ API IBKR do aplikacji" OFF)
set(
    IBKR_TWS_API_ROOT
    "C:/TWS API 1050.02"
    CACHE PATH
    "Folder instalacyjny IBKR TWS API"
)
set(
    IBKR_BID_LIBRARY
    "${IBKR_TWS_API_ROOT}/source/cppclient/client/lib/libbid.lib"
    CACHE FILEPATH
    "Ścieżka do biblioteki Intel Decimal Floating-Point"
)

if(ENABLE_IBKR_TWS_API)
    if(NOT EXISTS "${IBKR_TWS_API_ROOT}/source/cppclient/client/CMakeLists.txt")
        message(FATAL_ERROR
            "Nie znaleziono C++ API IBKR w '${IBKR_TWS_API_ROOT}'. "
            "Ustaw IBKR_TWS_API_ROOT na folder instalacyjny TWS API."
        )
    endif()
    if(NOT EXISTS "${IBKR_BID_LIBRARY}")
        message(FATAL_ERROR
            "Nie znaleziono biblioteki Intel Decimal Floating-Point "
            "w '${IBKR_BID_LIBRARY}'."
        )
    endif()

    find_package(Protobuf 36.2.0 EXACT CONFIG REQUIRED)
    add_subdirectory(
        "${IBKR_TWS_API_ROOT}/source/cppclient/client"
        "${CMAKE_BINARY_DIR}/ibkr-twsapi"
    )
    file(GLOB IBKR_PROTOBUF_SOURCES CONFIGURE_DEPENDS
        "${IBKR_TWS_API_ROOT}/source/cppclient/client/protobuf/*.pb.cc"
    )
    target_sources(twsapi PRIVATE ${IBKR_PROTOBUF_SOURCES})
    target_include_directories(twsapi PUBLIC
        "${IBKR_TWS_API_ROOT}/source/cppclient/client/protobuf"
    )
    target_link_libraries(twsapi
        PUBLIC protobuf::libprotobuf
        PRIVATE "${IBKR_BID_LIBRARY}"
    )
    set_target_properties(twsapi PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF
    )
endif()

add_executable(gieda WIN32
    src/main.cpp
    src/pages/MainWindow/MainWindow.cpp
    src/pages/reports/ReportsPage.cpp
    src/pages/reports/CandleGapsPage.cpp
    src/pages/reports/SignalsPage.cpp
    src/pages/reports/TransactionsPage.cpp
    src/stockmarket/StockPriceMonitor.cpp
    src/stockmarket/CandlePromptContextBuilder.cpp
    src/pages/MainWindow/MainWindowDisplay.cpp
    src/stockmarket/StockMarketClient/TwelveDataClient.cpp
    src/stockmarket/StockMarketClient/CandlesDataClient.cpp
    src/stockmarket/StockMarketClient/CandleHistoryClient.cpp
    src/stockmarket/StockMarketClient/CandleBackfillClient.cpp
    src/utility/DailySentencePicker.cpp
    src/utility/AudioSignal.cpp
    src/utility/ErrorLogger.cpp
    src/utility/todo/TodoTask.cpp
    src/utility/todo/ViewRegistry.cpp
    src/utility/todo/TodoRepository.cpp
    src/utility/todo/TodoManager.cpp
    src/utility/todo/TodoScheduler.cpp
    src/utility/todo/TodoView.cpp
    src/algorithms/Grok-45/EURUSD/SignalGenerator.cpp
)
set_property(TARGET gieda PROPERTY CXX_SCAN_FOR_MODULES OFF)
target_include_directories(gieda PRIVATE
    src/stockmarket
    src/pages/MainWindow
    src/pages/reports
    src/stockmarket/StockMarketClient
    src/utility
    src/utility/todo
    src/algorithms/Grok-45/EURUSD
)
target_link_libraries(gieda PRIVATE
    comctl32
    winmm
    winhttp
)
if(ENABLE_IBKR_TWS_API)
    target_include_directories(gieda PRIVATE
        "${IBKR_TWS_API_ROOT}/source/cppclient/client"
    )
    target_link_libraries(gieda PRIVATE twsapi)
    add_custom_command(TARGET gieda POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_RUNTIME_DLLS:gieda>
            "$<TARGET_FILE_DIR:gieda>"
        COMMAND_EXPAND_LISTS
    )
endif()
target_link_options(gieda PRIVATE -static-libgcc -static-libstdc++)

get_filename_component(MINGW_COMPILER_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
add_custom_command(TARGET gieda POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${MINGW_COMPILER_DIR}/libwinpthread-1.dll"
        "$<TARGET_FILE_DIR:gieda>/libwinpthread-1.dll"
)
if(ENABLE_IBKR_TWS_API)
    add_custom_command(TARGET gieda POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${MINGW_COMPILER_DIR}/libgcc_s_seh-1.dll"
            "$<TARGET_FILE_DIR:gieda>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${MINGW_COMPILER_DIR}/libstdc++-6.dll"
            "$<TARGET_FILE_DIR:gieda>"
    )
endif()

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

    add_executable(candle_prompt_context_tests
        src/stockmarket/CandlePromptContextBuilder.cpp
        src/stockmarket/StockMarketClient/CandleHistoryClient.cpp
        src/utility/ErrorLogger.cpp
        src/stockmarket/tests/CandlePromptContextBuilderTests.cpp
    )
    set_property(
        TARGET candle_prompt_context_tests
        PROPERTY CXX_SCAN_FOR_MODULES OFF
    )
    target_include_directories(candle_prompt_context_tests PRIVATE
        src/stockmarket
        src/stockmarket/StockMarketClient
        src/utility
    )
    target_link_libraries(candle_prompt_context_tests PRIVATE winhttp)
    target_link_options(candle_prompt_context_tests PRIVATE
        -static-libgcc
        -static-libstdc++
    )
    add_custom_command(TARGET candle_prompt_context_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${MINGW_COMPILER_DIR}/libwinpthread-1.dll"
            "$<TARGET_FILE_DIR:candle_prompt_context_tests>/libwinpthread-1.dll"
    )
    add_test(
        NAME candle_prompt_context_tests
        COMMAND candle_prompt_context_tests
    )

    add_executable(daily_sentence_picker_tests
        src/utility/DailySentencePicker.cpp
        src/stockmarket/StockMarketClient/CandleHistoryClient.cpp
        src/utility/ErrorLogger.cpp
        src/utility/tests/DailySentencePickerTests.cpp
    )
    set_property(
        TARGET daily_sentence_picker_tests
        PROPERTY CXX_SCAN_FOR_MODULES OFF
    )
    target_include_directories(daily_sentence_picker_tests PRIVATE
        src/stockmarket/StockMarketClient
        src/utility
    )
    target_link_libraries(daily_sentence_picker_tests PRIVATE winhttp)
    target_link_options(daily_sentence_picker_tests PRIVATE
        -static-libgcc
        -static-libstdc++
    )
    add_test(
        NAME daily_sentence_picker_tests
        COMMAND daily_sentence_picker_tests
    )

    add_executable(todo_model_tests
        src/utility/todo/TodoTask.cpp
        src/utility/todo/ViewRegistry.cpp
        src/utility/todo/tests/TodoModelTests.cpp
    )
    set_property(
        TARGET todo_model_tests
        PROPERTY CXX_SCAN_FOR_MODULES OFF
    )
    target_include_directories(todo_model_tests PRIVATE
        src/utility/todo
    )
    target_link_options(todo_model_tests PRIVATE
        -static-libgcc
        -static-libstdc++
    )
    add_test(
        NAME todo_model_tests
        COMMAND todo_model_tests
    )
endif()
