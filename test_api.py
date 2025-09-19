#!/usr/bin/env python3
"""
Test script for the Video Editor API.
"""

import asyncio
import httpx
import json
from pathlib import Path


async def test_api_endpoints():
    """Test the API endpoints."""
    
    base_url = "http://localhost:8000"
    
    print("🧪 Testing Video Editor API")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Test root endpoint
        print("1. Testing root endpoint...")
        try:
            response = await client.get(f"{base_url}/")
            print(f"   Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"   Response: {data['message']}")
            print("   ✅ Root endpoint working")
        except Exception as e:
            print(f"   ❌ Root endpoint failed: {e}")
        
        # Test health endpoint
        print("\n2. Testing health endpoint...")
        try:
            response = await client.get(f"{base_url}/health")
            print(f"   Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"   Response: {data}")
            print("   ✅ Health endpoint working")
        except Exception as e:
            print(f"   ❌ Health endpoint failed: {e}")
        
        # Test transcription info endpoint
        print("\n3. Testing transcription info endpoint...")
        try:
            response = await client.get(f"{base_url}/api/info")
            print(f"   Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"   Service: {data['service']}")
                print(f"   API URL: {data['api_url']}")
                print(f"   Mock mode: {data['mock_mode']}")
            print("   ✅ Info endpoint working")
        except Exception as e:
            print(f"   ❌ Info endpoint failed: {e}")
        
        # Test transcription endpoint with audio file
        print("\n4. Testing transcription endpoint...")
        audio_file = "mpeg_test.wav"
        
        if Path(audio_file).exists():
            try:
                with open(audio_file, 'rb') as f:
                    files = {'file': (audio_file, f, 'audio/wav')}
                    data = {'language': 'en'}
                    
                    response = await client.post(
                        f"{base_url}/api/transcribe",
                        files=files,
                        data=data
                    )
                    
                    print(f"   Status: {response.status_code}")
                    
                    if response.status_code == 200:
                        result = response.json()
                        print(f"   Transcript: {result['transcript'][:100]}...")
                        print(f"   Duration: {result['duration']}s")
                        print(f"   Words: {len(result['words'])}")
                        print(f"   Segments: {len(result['segments'])}")
                        print("   ✅ Transcription endpoint working")
                    else:
                        print(f"   ❌ Transcription failed: {response.text}")
                        
            except Exception as e:
                print(f"   ❌ Transcription endpoint failed: {e}")
        else:
            print(f"   ⚠️  Audio file {audio_file} not found, skipping transcription test")


async def main():
    """Main test function."""
    print("Starting API tests...")
    print("Make sure the API server is running on http://localhost:8000")
    print("Run: python main.py")
    print()
    
    await test_api_endpoints()
    
    print("\n" + "=" * 50)
    print("🎉 API testing completed!")


if __name__ == "__main__":
    asyncio.run(main())

