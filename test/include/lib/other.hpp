#pragma once

#include "lib/include.h"
#include <string>

std::string getGreeting();

#define GREETING (getGreeting())
