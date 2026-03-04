#!/usr/bin/env python3
"""
Test script for Whisper API with fallback to mock data for development.
This script tries the real API first, and if it fails, uses mock data.
"""

import asyncio
import httpx
import json
import sys
import os
from pathlib import Path
import dotenv
from mock_whisper_response import create_mock_whisper_response, load_mock_response_from_file

# Load environment variables
dotenv.load_dotenv()


async def test_whisper_api_real(audio_file_path: str, api_url: str = "https://testsucceed.com/whisper"):
    """
    Try to send audio file to the real Whisper API.
    
    Args:
        audio_file_path (str): Path to the audio file
        api_url (str): URL of the Whisper API endpoint
    
    Returns:
        dict: API response or None if failed
    """
    
    # Check if audio file exists
    if not Path(audio_file_path).exists():
        print(f"❌ Error: Audio file '{audio_file_path}' not found!")
        return None
    
    print(f"🌐 Attempting to connect to real Whisper API...")
    print(f"📁 Audio file: {audio_file_path}")
    print(f"🔗 API URL: {api_url}")
    print("-" * 50)
    
    try:
        # Read the audio file
        with open(audio_file_path, 'rb') as audio_file:
            audio_data = audio_file.read()
        
        # Prepare the file for upload with correct parameters
        files = {
            'file': (Path(audio_file_path).name, audio_data, 'audio/wav')
        }
        
        # Add language parameter
        params = {'language': 'en'}
        
        # Send POST request to the API using httpx
        print("📤 Sending request to Whisper API...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, files=files, params=params)
            
            print(f"📊 Response status code: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    print("✅ Success! Real API response received:")
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    return result
                except json.JSONDecodeError:
                    print("❌ Error: Response is not valid JSON")
                    print(f"Raw response: {response.text}")
                    return None
            else:
                print(f"❌ API returned status code {response.status_code}")
                print(f"Response: {response.text}")
                return None
                
    except httpx.TimeoutException:
        print("⏰ Request timed out")
        return None
    except httpx.ConnectError:
        print("🔌 Could not connect to the API")
        return None
    except httpx.HTTPStatusError as e:
        print(f"🚫 HTTP error: {e}")
        return None
    except httpx.RequestError as e:
        print(f"❌ Request error: {e}")
        return None
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        return None


async def test_whisper_api_with_fallback(audio_file_path: str, api_url: str = "https://testsucceed.com/whisper"):
    """
    Test Whisper API with fallback to mock data.
    
    Args:
        audio_file_path (str): Path to the audio file
        api_url (str): URL of the Whisper API endpoint
    
    Returns:
        dict: API response (real or mock)
    """
    
    print("🎵 Whisper API Test with Fallback")
    print("=" * 60)
    
    # Try real API first
    result = await test_whisper_api_real(audio_file_path, api_url)
    
    if result is not None:
        print("\n🎉 Using REAL API response!")
        return result
    
    # Fallback to mock data
    print("\n" + "=" * 60)
    print("🎭 Real API unavailable, using MOCK data for development...")
    print("=" * 60)
    
    # Try to load existing mock response, or create a new one
    try:
        result = load_mock_response_from_file("mock_whisper_response.json")
        print("📂 Loaded existing mock response from file")
    except:
        print("🆕 Creating new mock response...")
        result = create_mock_whisper_response()
    
    print("✅ Mock response ready for development!")
    return result


def analyze_response(result: dict):
    """Analyze and display the response data."""
    if not result:
        print("❌ No response data to analyze")
        return
    
    print("\n" + "=" * 60)
    print("📊 Response Analysis:")
    print("=" * 60)
    
    # Display transcript
    if 'transcript' in result:
        print(f"📝 Transcript: '{result['transcript']}'")
    
    # Display duration
    if 'duration' in result:
        print(f"⏱️  Duration: {result['duration']} seconds")
    
    # Display word count and timestamps
    if 'words' in result:
        words = result['words']
        print(f"🔤 Number of words: {len(words)}")
        print("\n📅 Word-level timestamps:")
        
        for i, word in enumerate(words[:10]):  # Show first 10 words
            if isinstance(word, dict) and 'word' in word and 'start' in word and 'end' in word:
                confidence = word.get('confidence', 'N/A')
                print(f"  {i+1:2d}. '{word['word']}' - {word['start']:.2f}s to {word['end']:.2f}s (confidence: {confidence})")
        
        if len(words) > 10:
            print(f"  ... and {len(words) - 10} more words")
    
    # Display segments if available
    if 'segments' in result:
        print(f"\n📑 Number of segments: {len(result['segments'])}")
    
    # Display metadata
    if 'metadata' in result:
        metadata = result['metadata']
        print(f"\n🔧 Metadata:")
        for key, value in metadata.items():
            print(f"  {key}: {value}")
    
    # Display all available keys
    print(f"\n🔑 Available keys in response: {list(result.keys())}")


async def main():
    """Main function to run the test."""
    audio_file = "mpeg_test.wav"
    
    # Test with fallback
    result = await test_whisper_api_with_fallback(audio_file)
    
    if result:
        analyze_response(result)
        
        # Save the result for further use
        with open("whisper_test_result.json", 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Result saved to 'whisper_test_result.json'")
        
        print(f"\n✅ Test completed successfully!")
    else:
        print("\n❌ Test failed - no response received")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
