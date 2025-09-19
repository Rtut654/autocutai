#!/usr/bin/env python3
"""
Mock Whisper API response for development and testing purposes.
This simulates the expected response format from the Whisper API.
"""

import json
import time
from typing import Dict, List, Any


def create_mock_whisper_response(transcript_text: str = None) -> Dict[str, Any]:
    """
    Create a mock response that simulates the Whisper API response format.
    
    Args:
        transcript_text (str): Custom transcript text. If None, uses default.
    
    Returns:
        Dict containing mock transcription data with word-level timestamps
    """
    
    if transcript_text is None:
        transcript_text = "Hello, this is a test audio file for the video editor API. We are testing word-level timestamps from the Whisper API."
    
    # Split the transcript into words
    words = transcript_text.split()
    
    # Generate mock timestamps (assuming each word takes about 0.5-1.5 seconds)
    word_data = []
    current_time = 0.0
    
    for i, word in enumerate(words):
        # Random duration between 0.3 and 1.2 seconds per word
        import random
        duration = random.uniform(0.3, 1.2)
        
        word_info = {
            "word": word.strip(".,!?"),  # Remove punctuation
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
            "confidence": round(random.uniform(0.85, 0.99), 3)
        }
        
        word_data.append(word_info)
        current_time += duration
    
    # Create the mock response structure
    mock_response = {
        "transcript": transcript_text,
        "language": "en",
        "duration": round(current_time, 2),
        "words": word_data,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": round(current_time, 2),
                "text": transcript_text,
                "words": word_data
            }
        ],
        "metadata": {
            "model": "whisper-1",
            "processing_time": round(random.uniform(2.5, 5.0), 2),
            "timestamp": time.time()
        }
    }
    
    return mock_response


def save_mock_response_to_file(response: Dict[str, Any], filename: str = "mock_whisper_response.json"):
    """Save the mock response to a JSON file for testing."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(response, f, indent=2, ensure_ascii=False)
    print(f"Mock response saved to {filename}")


def load_mock_response_from_file(filename: str = "mock_whisper_response.json") -> Dict[str, Any]:
    """Load a mock response from a JSON file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"File {filename} not found. Creating a new mock response.")
        response = create_mock_whisper_response()
        save_mock_response_to_file(response, filename)
        return response


if __name__ == "__main__":
    # Create and display a mock response
    print("🎭 Creating Mock Whisper API Response")
    print("=" * 50)
    
    mock_response = create_mock_whisper_response()
    
    print("📝 Mock Transcript:")
    print(f"'{mock_response['transcript']}'")
    print(f"\n⏱️  Duration: {mock_response['duration']} seconds")
    print(f"🔤 Number of words: {len(mock_response['words'])}")
    
    print("\n📅 Word-level timestamps:")
    for i, word in enumerate(mock_response['words'][:10]):  # Show first 10 words
        print(f"  {i+1:2d}. '{word['word']}' - {word['start']:.2f}s to {word['end']:.2f}s (confidence: {word['confidence']:.3f})")
    
    if len(mock_response['words']) > 10:
        print(f"  ... and {len(mock_response['words']) - 10} more words")
    
    # Save to file
    save_mock_response_to_file(mock_response)
    
    print(f"\n✅ Mock response created successfully!")
    print(f"📊 Processing time: {mock_response['metadata']['processing_time']}s")

