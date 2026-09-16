#pragma once

#include <Arduino.h>

#include "frame_types.h"

namespace deskmate {

uint16_t crc16CcittFalse(const uint8_t* data, size_t length);

// Encodes header + payload + CRC using COBS and appends a 0x00 delimiter.
// Returns false when the payload cannot fit in the bounded firmware buffer.
bool writeUartFrame(Stream& output, uint8_t type, uint16_t sequence, uint32_t timestamp_ms,
                    const uint8_t* payload, uint16_t payload_length);

}  // namespace deskmate
