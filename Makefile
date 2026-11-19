CXX      = g++
CXXFLAGS = -std=c++17 -O2 -Wall -Wextra -Iinclude

SRCS   = $(wildcard src/*.cpp)
OBJS   = $(patsubst src/%.cpp,build/%.o,$(SRCS))
TARGET = build/l4s_router

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^

# Every object is rebuilt if any header changes (simple, project is small)
build/%.o: src/%.cpp include/*.hpp | build
	$(CXX) $(CXXFLAGS) -c $< -o $@

build:
	mkdir -p build

clean:
	rm -rf build

.PHONY: all clean
