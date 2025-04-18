from flask import Flask, request, jsonify
import requests
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

SEARCH_AGENT_URL = "http://localhost:5010"
RAG_AGENT_URL = "http://localhost:5006"

# Agent Card metadata for the Gateway Agent
AGENT_CARD = {
    "name": "GatewayAgent",
    "description": "Routes requests to either a Search Agent or a RAG Agent based on keywords.",
    "url": "http://localhost:5005",  # base URL where this gateway is hosted
    "version": "1.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False
    }
}

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

    # Determine target agent based on keywords
    if ("use my local rag system" in user_text.lower() or 
        "use my local rag server" in user_text.lower()):
        target_agent_url = RAG_AGENT_URL
        print(f"Routing task {task_id} to RAG Agent ({target_agent_url})")
    else:
        target_agent_url = SEARCH_AGENT_URL
        print(f"Routing task {task_id} to Search Agent ({target_agent_url})")

    # Forward the task to the selected agent
    target_send_url = f"{target_agent_url}/tasks/send"
    try:
        response = requests.post(target_send_url, json=task_request, timeout=60) # Added timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
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