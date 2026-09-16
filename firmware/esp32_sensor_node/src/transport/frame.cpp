#include "frame.h"

namespace deskmate {
namespace {

constexpr size_t kMaxDecodedFrame = kFrameHeaderSize + kFrameMaxPayload + kFrameCrcSize;
// Worst case COBS overhead is one byte per 254 input bytes plus the delimiter.
constexpr size_t kMaxEncodedFrame = kMaxDecodedFrame + (kMaxDecodedFrame / 254) + 2;

void putU16Le(uint8_t* output, uint16_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFF);
  output[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

void putU32Le(uint8_t* output, uint32_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFF);
  output[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  output[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
  output[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

size_t cobsEncode(const uint8_t* input, size_t length, uint8_t* output) {
  size_t read_index = 0;
  size_t write_index = 1;
  size_t code_index = 0;
  uint8_t code = 1;

  while (read_index < length) {
    if (input[read_index] == 0) {
      output[code_index] = code;
      code = 1;
      code_index = write_index++;
      ++read_index;
    } else {
      output[write_index++] = input[read_index++];
      ++code;
      if (code == 0xFF) {
        output[code_index] = code;
        code = 1;
        code_index = write_index++;
      }
    }
  }
  output[code_index] = code;
  return write_index;
}

}  // namespace

uint16_t crc16CcittFalse(const uint8_t* data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) != 0 ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                                : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

bool writeUartFrame(Stream& output, uint8_t type, uint16_t sequence, uint32_t timestamp_ms,
                    const uint8_t* payload, uint16_t payload_length) {
  if (payload_length > kFrameMaxPayload || (payload_length != 0 && payload == nullptr)) {
    return false;
  }

  uint8_t decoded[kMaxDecodedFrame];
  decoded[0] = kFrameSof;
  decoded[1] = kFrameVersion;
  decoded[2] = type;
  putU16Le(decoded + 3, sequence);
  putU16Le(decoded + 5, payload_length);
  putU32Le(decoded + 7, timestamp_ms);
  for (uint16_t i = 0; i < payload_length; ++i) decoded[kFrameHeaderSize + i] = payload[i];

  const size_t crc_offset = kFrameHeaderSize + payload_length;
  putU16Le(decoded + crc_offset, crc16CcittFalse(decoded, crc_offset));

  uint8_t encoded[kMaxEncodedFrame];
  const size_t encoded_length = cobsEncode(decoded, crc_offset + kFrameCrcSize, encoded);
  if (encoded_length + 1 > sizeof(encoded)) return false;
  output.write(encoded, encoded_length);
  output.write(static_cast<uint8_t>(0));
  return true;
}

}  // namespace deskmate
