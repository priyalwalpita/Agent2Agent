from flask import Flask, request, jsonify
import requests
import uuid
import os
import openai
from dotenv import load_dotenv

load_dotenv()

# Load OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("Warning: OPENAI_API_KEY environment variable not set. Routing will default to Search.")
    # Optionally raise an error: raise ValueError("OPENAI_API_KEY environment variable not set.")

app = Flask(__name__)

SEARCH_AGENT_URL = "http://localhost:5010"
RAG_AGENT_URL = "http://localhost:5006"

# Agent Card metadata for the Gateway Agent
AGENT_CARD = {
    "name": "GatewayAgent",
    "description": "Routes requests to either a Search Agent or a RAG Agent using AI.",
    "url": "http://localhost:5005",  # base URL where this gateway is hosted
    "version": "1.1",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False
    }
}

# --- New Function: OpenAI Routing Logic ---
def route_query_with_openai(user_text: str) -> str:
    """Uses OpenAI to determine if the query should go to RAG or Search."""
    if not openai.api_key: # Check if API key is loaded
         print("OpenAI API key not available. Defaulting to Search.")
         return SEARCH_AGENT_URL

    prompt = f"""
You are an intelligent request router. You need to decide whether to route a user's query to a 'RAG Agent' or a 'Search Agent'.
- The 'RAG Agent' answers questions based *only* on a specific local knowledge base (e.g., documents provided to it). Use this agent if the query explicitly mentions using local documents, a specific knowledge base, or seems highly specific to a contained set of information.
- The 'Search Agent' answers questions using a general web search engine. Use this agent for general knowledge questions, current events, or anything not specifically tied to the local knowledge base.

User query: "{user_text}"

Based on the query, which agent is more appropriate? Respond with ONLY 'RAG' or 'SEARCH'.
"""
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo", # You can change the model if needed
            messages=[
                {"role": "system", "content": "You are an intelligent request router helping to decide between a RAG agent (local knowledge) and a Search agent (web search). Respond ONLY with 'RAG' or 'SEARCH'."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=10, # Slightly increased for safety
            temperature=0 # For deterministic output
        )
        choice = response.choices[0].message.content.strip().upper()
        
        if "RAG" in choice: # Check if RAG is mentioned
            print(f"OpenAI routing decision: RAG (based on response: '{choice}')")
            return RAG_AGENT_URL
        elif "SEARCH" in choice: # Check if SEARCH is mentioned
            print(f"OpenAI routing decision: SEARCH (based on response: '{choice}')")
            return SEARCH_AGENT_URL
        else:
            print(f"Warning: OpenAI returned unexpected choice: '{choice}'. Defaulting to Search.")
            return SEARCH_AGENT_URL
            
    except Exception as e:
        print(f"Error calling OpenAI for routing: {e}. Defaulting to Search.")
        # Optional: Implement fallback to keyword logic here if desired
        # if ("use my local rag system" in user_text.lower() or
        #     "use my local rag server" in user_text.lower()):
        #     return RAG_AGENT_URL
        return SEARCH_AGENT_URL

# Endpoint to serve the Gateway Agent Card
@app.get("/.well-known/agent.json")
def get_agent_card():
    return jsonify(AGENT_CARD)

# Endpoint to handle and route task requests
@app.post("/tasks/send")
def handle_task():
    task_request = request.get_json()
    if not task_request:
        return jsonify({"error": "Invalid request"}), 400

    task_id = task_request.get("id")
    if not task_id:
         task_id = str(uuid.uuid4()) # Generate one if missing, though A2A spec implies it should exist
         task_request['id'] = task_id

    # Extract user's message text from the request
    try:
        user_text = task_request["message"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        print(f"Error extracting user text: {e}") # Log error
        return jsonify({"error": "Bad message format"}), 400

    # --- Determine target agent using OpenAI ---
    target_agent_url = route_query_with_openai(user_text)
    print(f"Routing task {task_id} via OpenAI decision to Agent ({target_agent_url})")
    # --- End OpenAI Routing ---


    # Forward the task to the selected agent
    target_send_url = f"{target_agent_url}/tasks/send"
    try:
        response = requests.post(target_send_url, json=task_request, timeout=60)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error forwarding task {task_id} to {target_agent_url}: {e}") # Log error
        # Return an error task response to the original client
        error_response_task = {
            "id": task_id,
            "status": {"state": "failed", "reason": f"Failed to contact downstream agent: {target_agent_url}"},
            "messages": [
                task_request.get("message", {}), # include original user message
                 {
                    "role": "agent",
                    "parts": [{"text": f"Error: Could not reach the target agent at {target_agent_url}. Details: {e}"}]
                }
            ]
        }
        return jsonify(error_response_task), 502 # Bad Gateway
    
    # Return the response from the downstream agent
    print(f"Received response for task {task_id} from {target_agent_url}")
    return jsonify(response.json())


if __name__ == "__main__":
    # Ensure port is integer
    port = int(os.environ.get("PORT", 5005))
    print(f"Gateway server starting on port {port}")
    app.run(host="0.0.0.0", port=port) 