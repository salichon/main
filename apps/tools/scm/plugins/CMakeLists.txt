MACRO(MAKE_MONITOR_PLUGIN _plugin)
	SET(MONITOR_PLUGIN_HEADERS ${_plugin}.h)
	SET(MONITOR_PLUGIN_SOURCES ${_plugin}.cpp)
	SC_ADD_PLUGIN_LIBRARY(MONITOR_PLUGIN ${_plugin} monitor)
	SC_LINK_LIBRARIES_INTERNAL(${_plugin} client mplugin)
ENDMACRO(MAKE_MONITOR_PLUGIN)

MAKE_MONITOR_PLUGIN(memailplugin)
MAKE_MONITOR_PLUGIN(mtextplugin)

# Optional Ncurses package
FIND_PACKAGE(Ncurses)

IF (NCURSES_FOUND)
    SET(MNCURSES_PLUGIN mncursesplugin)
    INCLUDE_DIRECTORIES(${NCURSES_INCLUDE_DIR})
    MAKE_MONITOR_PLUGIN(${MNCURSES_PLUGIN})
    TARGET_LINK_LIBRARIES(${MNCURSES_PLUGIN} ${NCURSES_LIBRARY})
    # TARGET_LINK_LIBRARIES(memailplugin ${NCURSES_LIBRARY})
ENDIF (NCURSES_FOUND)

