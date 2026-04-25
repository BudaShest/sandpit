"""Frame extraction from TCP stream."""
import asyncio
import struct
from typing import AsyncGenerator, Optional
from app.settings import settings


class FrameExtractor:
    """Extract frames from TCP stream."""
    
    def __init__(self, max_frame_size: int = None):
        self.max_frame_size = max_frame_size or settings.frame_max_size
        self.buffer = bytearray()

    @staticmethod
    def _looks_like_flex_emulator_tail(buf: bytearray) -> bool:
        """
        Heuristic for emulator packets that may not include trailing 0x7E.
        Observed shape: starts with 0x7E 0x54 ... and ends with CRC byte often 0x7F.
        """
        if len(buf) < 40:
            return False
        if buf[0] != 0x7E:
            return False
        if buf[1] not in (0x54, ord("T"), ord("A")):
            return False
        return buf[-1] in (0x7F, 0x7E)
    
    async def frame_stream(self, reader: asyncio.StreamReader) -> AsyncGenerator[bytes, None]:
        """Extract frames from stream."""
        while True:
            try:
                # Read chunk with timeout
                chunk = await asyncio.wait_for(
                    reader.read(self.max_frame_size),
                    timeout=settings.read_timeout
                )
                
                if not chunk:
                    break
                
                self.buffer.extend(chunk)
                
                # Try to extract frames from buffer
                while True:
                    frame = self._extract_frame()
                    if frame is None:
                        break
                    yield frame
                    
            except asyncio.TimeoutError:
                # If some bytes were received but no frame delimiter appeared yet,
                # flush buffer as a raw frame for diagnostics/fallback parsing.
                if self.buffer:
                    frame = bytes(self.buffer)
                    self.buffer.clear()
                    yield frame
                continue
            except Exception as e:
                print(f"Frame extraction error: {e}")
                break
    
    def _extract_frame(self) -> Optional[bytes]:
        """Extract single frame from buffer."""
        if len(self.buffer) < 4:
            return None
        
        # Try to find frame start marker (0x7E for Navtelecom)
        start_idx = self.buffer.find(0x7E)
        if start_idx == -1:
            # No marker yet: keep accumulating, but cap buffer growth.
            if len(self.buffer) > self.max_frame_size:
                self.buffer = self.buffer[-self.max_frame_size:]
            return None
        
        # Remove data before start marker
        if start_idx > 0:
            self.buffer = self.buffer[start_idx:]
        
        # Check if we have enough data for frame header
        if len(self.buffer) < 4:
            return None
        
        # Try to parse frame length (this is protocol-specific)
        # For now, use simple approach - read until next 0x7E or max size
        end_idx = self.buffer.find(0x7E, 1)
        
        if end_idx == -1:
            # No end marker found
            if self._looks_like_flex_emulator_tail(self.buffer):
                # FLEX emulator packet can be complete without explicit trailing 0x7E.
                frame = bytes(self.buffer)
                self.buffer.clear()
                return frame
            if len(self.buffer) >= self.max_frame_size:
                # Frame too large, return what we have
                frame = bytes(self.buffer[:self.max_frame_size])
                self.buffer = self.buffer[self.max_frame_size:]
                return frame
            return None
        
        # Extract frame
        frame = bytes(self.buffer[:end_idx + 1])
        self.buffer = self.buffer[end_idx + 1:]
        
        return frame


async def frame_stream(reader: asyncio.StreamReader) -> AsyncGenerator[bytes, None]:
    """Извлечение кадров: отдельный буфер на соединение (общий singleton ломал бы несколько клиентов)."""
    extractor = FrameExtractor()
    async for frame in extractor.frame_stream(reader):
        yield frame
