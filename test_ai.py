import os
from google.genai import Client

client = Client(api_key=os.getenv("GEMINI_API_KEY") or "AIzaSyBYRVXCPQcElqkxHrHyNNvV0suNajXrEuQ")

response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="Explain mutual funds simply"
)

print(response.text)