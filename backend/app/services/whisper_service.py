"""
Whisper service for audio transcription with word-level timestamps.
This service handles communication with the Whisper API and provides fallback to mock data.
"""

import asyncio
import httpx
import json
import os
import logging
from typing import Dict, Any, Optional
from pathlib import Path
import dotenv

# Load environment variables
dotenv.load_dotenv()

logger = logging.getLogger(__name__)


class WhisperService:
    """Service for handling Whisper API requests."""
    
    def __init__(self, api_url: str = None):
        """
        Initialize the Whisper service.
        
        Args:
            api_url (str): URL of the Whisper API endpoint
        """
        self.api_url = api_url or os.getenv("WHISPER_API_URL", "https://testsucceed.com/whisper")
        self.timeout = 60.0
        
        logger.info(f"WhisperService initialized with API URL: {self.api_url}")
    
    async def transcribe_audio(self, audio_data: bytes, filename: str, language: str = "en") -> Dict[str, Any]:
        """
        Transcribe audio data and return word-level timestamps.
        
        Args:
            audio_data (bytes): Audio file data
            filename (str): Original filename
            language (str): Language code (default: "en")
        
        Returns:
            Dict containing transcription with word-level timestamps
        """
        
        return await self._call_real_api(audio_data, filename, language)
    
    async def _call_real_api(self, audio_data: bytes, filename: str, language: str) -> Dict[str, Any]:
        """
        Call the real Whisper API.
        
        Args:
            audio_data (bytes): Audio file data
            filename (str): Original filename
            language (str): Language code
        
        Returns:
            Dict containing transcription with word-level timestamps
        """
        
        files = {
            'file': (filename, audio_data, 'audio/wav')
        }
        
        params = {'language': language}
        
        logger.info(f"Sending request to Whisper API: {self.api_url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.api_url, files=files, params=params)
            
            logger.info(f"API response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info("Successfully received transcription from API")
                return result
            else:
                logger.error(f"API returned status {response.status_code}: {response.text}")
                raise Exception(f"API returned status {response.status_code}")
    
    
    async def transcribe_file(self, file_path: str, language: str = "en") -> Dict[str, Any]:
        """
        Transcribe an audio file from disk.
        
        Args:
            file_path (str): Path to the audio file
            language (str): Language code (default: "en")
        
        Returns:
            Dict containing transcription with word-level timestamps
        """
        
        if not Path(file_path).exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        with open(file_path, 'rb') as f:
            audio_data = f.read()
        
        filename = Path(file_path).name
        return await self.transcribe_audio(audio_data, filename, language)


# Global service instance
whisper_service = WhisperService()


async def transcribe_audio(audio_data: bytes, filename: str, language: str = "en") -> Dict[str, Any]:
    """
    Convenience function to transcribe audio data.
    
    Args:
        audio_data (bytes): Audio file data
        filename (str): Original filename
        language (str): Language code (default: "en")
    
    Returns:
        Dict containing transcription with word-level timestamps
    """
    return await whisper_service.transcribe_audio(audio_data, filename, language)


async def transcribe_file(file_path: str, language: str = "en") -> Dict[str, Any]:
    """
    Convenience function to transcribe an audio file.
    
    Args:
        file_path (str): Path to the audio file
        language (str): Language code (default: "en")
    
    Returns:
        Dict containing transcription with word-level timestamps
    """
    return await whisper_service.transcribe_file(file_path, language)
