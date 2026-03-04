#!/usr/bin/env python3
"""
Test script to send audio file to Whisper API and get word-level timestamps.
"""

import asyncio
import httpx
import json
import sys
import os
from pathlib import Path
import dotenv

# Load environment variables
dotenv.load_dotenv()

async def test_whisper_api(audio_file_path, api_url="https://testsucceed.com/upload_audio?lang=en"):
    """
    Send audio file to Whisper API and get transcription with word-level timestamps.
    
    Args:
        audio_file_path (str): Path to the audio file
        api_url (str): URL of the Whisper API endpoint
    
    Returns:
        dict: API response containing transcription and timestamps
    """
    
    # Check if audio file exists
    if not Path(audio_file_path).exists():
        print(f"Error: Audio file '{audio_file_path}' not found!")
        return None
    
    print(f"Sending audio file: {audio_file_path}")
    print(f"API URL: {api_url}")
    print("-" * 50)
    
    try:
        # Read the audio file
        with open(audio_file_path, 'rb') as audio_file:
            audio_data = audio_file.read()
        
        # Prepare the file for upload with correct parameters
        files = {
            'file': ("voice.oga", audio_data, 'audio/mpeg')
        }
        
        # Send POST request to the API using httpx
        print("Sending request to Whisper API...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(api_url, files=files)
            
            print(f"Response status code: {response.status_code}")
            print(f"Response headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    print("✅ Success! API response:")
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    return result
                except json.JSONDecodeError:
                    print("❌ Error: Response is not valid JSON")
                    print(f"Raw response: {response.text}")
                    return None
            else:
                print(f"❌ Error: API returned status code {response.status_code}")
                print(f"Response text: {response.text}")
                return None
                
    except httpx.TimeoutException:
        print("❌ Error: Request timed out")
        return None
    except httpx.ConnectError:
        print("❌ Error: Could not connect to the API")
        return None
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP error occurred: {e}")
        print(f"Response text: {e.response.text}")
        return None
    except httpx.RequestError as e:
        print(f"❌ Request error occurred: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None


async def main():
    """Main function to run the test."""
    audio_file = "mpeg_test.wav"
    
    print("🎵 Whisper API Test Script")
    print("=" * 50)
    
    # Test the API
    result = await test_whisper_api(audio_file)
    
    if result:
        print("\n" + "=" * 50)
        print("📊 Analysis of the response:")
        
        # Try to extract useful information from the response
        if isinstance(result, dict):
            if 'transcript' in result:
                print(f"📝 Transcript: {result['transcript']}")
            
            if 'words' in result:
                print(f"🔤 Number of words: {len(result['words'])}")
                print("📅 Word-level timestamps:")
                for i, word in enumerate(result['words'][:5]):  # Show first 5 words
                    if isinstance(word, dict) and 'word' in word and 'start' in word and 'end' in word:
                        print(f"  {i+1}. '{word['word']}' - {word['start']:.2f}s to {word['end']:.2f}s")
                if len(result['words']) > 5:
                    print(f"  ... and {len(result['words']) - 5} more words")
            
            # Print all available keys
            print(f"\n🔑 Available keys in response: {list(result.keys())}")
    else:
        print("\n❌ Test failed - no valid response received")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())




