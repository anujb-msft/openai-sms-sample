from azure.communication.sms import SmsClient
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Union
import uvicorn
import logging
import os
import uuid
import shutil
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="SMS Receiver API")

# Set up directory for file uploads
UPLOAD_DIR = Path("uploads")
if not UPLOAD_DIR.exists():
    UPLOAD_DIR.mkdir(parents=True)

# Mount static files
# app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory conversation history storage
# Key: phone number, Value: list of message objects
conversation_history = {}

# In-memory storage for ID uploads
# Key: phone number, Value: list of file IDs
id_uploads = {}

# For sending SMS (existing functionality)
def get_sms_client():
    """
    Get SMS client using DefaultAzureCredential or connection string
    """
    try:
        # If connection string is available in environment variable
        connection_string = os.environ.get("AZURE_COMMUNICATION_SERVICE_CONNECTION_STRING")
        if connection_string:
            logger.info("Creating SMS client using connection string")
            return SmsClient.from_connection_string(connection_string)
        
        # Otherwise, use DefaultAzureCredential
        endpoint = os.environ.get("AZURE_COMMUNICATION_SERVICE_ENDPOINT")
        if endpoint:
            logger.info(f"Creating SMS client using DefaultAzureCredential and endpoint: {endpoint}")
            return SmsClient(endpoint, DefaultAzureCredential())
        
        logger.error("No Azure Communication Services configuration found. Please set AZURE_COMMUNICATION_SERVICE_CONNECTION_STRING or AZURE_COMMUNICATION_SERVICE_ENDPOINT")
        return None
    except Exception as e:
        logger.error(f"Error creating SMS client: {str(e)}", exc_info=True)
        return None

# Pydantic model for SMS webhook payload
class SMSDeliveryReport(BaseModel):
    messageId: str
    to: str
    deliveryStatus: str
    deliveryStatusDetails: Optional[str] = None
    receivedTimestamp: Optional[str] = None
    
class SMSInboundMessage(BaseModel):
    messageId: str
    from_: str = None  # Sender's phone number
    to: str            # Your ACS phone number
    message: str       # The message content
    receivedTimestamp: str
    
class SMSEventPayload(BaseModel):
    resourceData: Dict[str, Any]  # Can be either delivery report or inbound message
    eventType: str

# Process SMS delivery report
async def process_sms_event(event_data: Union[Dict[str, Any], List[Dict[str, Any]]]):
    """
    Background task to process SMS events using Azure OpenAI to generate responses
    """
    logger.info(f"Processing SMS event (type: {type(event_data).__name__})")
    
    try:
        # Handle single event or each event in an array
        events_to_process = []
        
        # Check if the payload is an array of events
        if isinstance(event_data, list):
            logger.info(f"Processing batch of {len(event_data)} events")
            events_to_process.extend(event_data)
        else:
            logger.info("Processing single event")
            events_to_process.append(event_data)
        
        for event in events_to_process:
            # Log the event structure for debugging
            logger.info(f"Event structure: {event}")
            
            # Skip validation events
            if event.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
                logger.info("Skipping validation event")
                continue
                
            # Check if this is an inbound SMS event
            if event.get("eventType") == "Microsoft.Communication.SMSReceived":
                logger.info("Processing SMS received event")
                
                # The structure might vary, so let's try different paths
                resource_data = event.get("data", {})
                if not resource_data:
                    logger.warning("No data field found in event")
                    continue
                    
                logger.info(f"Resource data: {resource_data}")
                
                # Extract message content and sender information
                message_content = resource_data.get("message", "")
                sender = resource_data.get("from", "")
                
                if not message_content or not sender:
                    logger.warning("Missing message content or sender information")
                    continue
                
                try:
                    # Initialize the Azure OpenAI client
                    client = AsyncAzureOpenAI(
                        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
                        api_key=os.environ.get("AZURE_OPENAI_KEY"),
                        api_version="2024-12-01-preview"
                    )
                    
                    # Get or initialize conversation history for this sender
                    if sender not in conversation_history:
                        conversation_history[sender] = [
                            {"role": "system", "content": 
                             """
You are a friendly and professional virtual assistant helping users fill out a form. Your task is to collect the following 5 pieces of information, one at a time, and confirm each before moving to the next:

1. First Name  
2. Last Name  
3. Phone Number (assume that the phone number they're messaging from is the correct one, but ask if they'd like to provide an alternate number)
4. Address
5. Annual Income

For each field:
- Ask clearly and politely for the information.
- Wait for the user to respond before proceeding to the next field.
- Confirm the input by repeating it back and asking if it's correct.
- If the user's response is unclear or incomplete, politely ask for clarification.
- For the phone number, first assume they want to use the phone number they're texting from. Ask "Would you like to use the current phone number you're texting from, or would you prefer to provide a different number?"

Be concise, helpful, and respectful at all times. Do not skip any fields. Once all five pieces of information are collected and confirmed, thank the user for completing the form."""
}
                        ]
                    
                    # Add the user's message to the conversation history
                    conversation_history[sender].append({"role": "user", "content": message_content})
                    
                    logger.info(f"Conversation history for {sender}: {conversation_history[sender]}")
                    
                    # Generate a response using Azure OpenAI with conversation history
                    response = await client.chat.completions.create(
                        model=os.environ.get("AZURE_OPENAI_MODEL"),  # Specify your deployed model name
                        messages=conversation_history[sender],
                        max_tokens=100
                    )
                    
                    # Extract the generated response
                    ai_response = response.choices[0].message.content.strip()
                    logger.info(f"Generated response: {ai_response}")
                    
                    # Add the assistant's response to the conversation history
                    conversation_history[sender].append({"role": "assistant", "content": ai_response})
                    
                    # Send the response back via SMS
                    phone_number = os.environ.get("PHONE_NUMBER", "")
                    logger.info(f"SMS Message Originator: {sender}")
                    logger.info(f"SMS Message Content: {message_content}")
                    logger.info(f"AI Response: {ai_response}")
                    logger.info(f"Sending SMS from: {phone_number} to: {sender}")

                    if not phone_number:
                        logger.error("PHONE_NUMBER environment variable is not set")
                        continue

                    sms_client = get_sms_client()
                    if sms_client:
                        try:
                            logger.info(f"Attempting to send SMS response...")
                            result = sms_client.send(
                                from_=phone_number,  # Your ACS phone number
                                to=[sender],
                                message=ai_response,
                                enable_delivery_report=True
                            )
                            
                            if isinstance(result, list):
                                for i, msg_result in enumerate(result):
                                    logger.info(f"SMS response sent. Message {i+1} ID: {msg_result.message_id}")
                            else:
                                logger.info(f"SMS response sent. Message ID: {result.message_id}")
                        except Exception as sms_error:
                            logger.error(f"Error sending SMS: {str(sms_error)}", exc_info=True)
                    else:
                        logger.error("Failed to initialize SMS client for sending response")
                except Exception as openai_error:
                    logger.error(f"Error generating AI response: {str(openai_error)}", exc_info=True)
                
    except Exception as e:
        logger.error(f"Error processing SMS event: {str(e)}", exc_info=True)

@app.get("/")
async def root():
    return {"message": "SMS Receiver API is up and running"}

@app.get("/api/conversations")
async def get_conversations():
    """
    Get a list of all phone numbers with active conversations
    """
    return {"phone_numbers": list(conversation_history.keys())}

@app.get("/api/conversations/{phone_number}")
async def get_conversation(phone_number: str):
    """
    Get the conversation history for a specific phone number
    """
    if phone_number in conversation_history:
        return {"conversation": conversation_history[phone_number]}
    else:
        return {"error": "No conversation found for this phone number"}

@app.delete("/api/conversations/{phone_number}")
async def delete_conversation(phone_number: str):
    """
    Delete the conversation history for a specific phone number
    """
    if phone_number in conversation_history:
        del conversation_history[phone_number]
        return {"message": f"Conversation for {phone_number} deleted"}
    else:
        return {"error": "No conversation found for this phone number"}

@app.post("/api/sms/webhook")
async def receive_sms_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint to receive SMS delivery reports and incoming messages from Azure Communication Services
    """
    try:
        # Get the raw payload
        payload = await request.json()
        logger.info(f"Received SMS webhook payload: {payload}")
        
        # Check if this is a validation event (Azure Event Grid subscription validation)
        if isinstance(payload, list) and len(payload) > 0:
            for event in payload:
                if event.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
                    validation_code = event.get("data", {}).get("validationCode")
                    if validation_code:
                        logger.info(f"Returning validation response for code: {validation_code}")
                        return {"validationResponse": validation_code}
            
            # For array of events, we don't need to check event_type here since we'll process them individually
            logger.info(f"Processing batch of {len(payload)} events")
            background_tasks.add_task(process_sms_event, payload)
            return {"status": "success", "message": f"Batch of {len(payload)} events received and processing"}
        
        # For single event format
        if payload.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
            validation_code = payload.get("data", {}).get("validationCode")
            if validation_code:
                logger.info(f"Returning validation response for code: {validation_code}")
                return {"validationResponse": validation_code}
        
        # Check the event type to determine how to process it (for single event)
        event_type = payload.get("eventType", "")
        
        if "Microsoft.Communication.SMSReceived" in event_type:
            logger.info("Processing inbound SMS message")
        elif "Microsoft.Communication.SMSDeliveryReportReceived" in event_type:
            logger.info("Processing SMS delivery report")
        else:
            logger.info(f"Processing unknown event type: {event_type}")
        
        # Process SMS event in the background
        background_tasks.add_task(process_sms_event, payload)
        
        return {"status": "success", "message": "SMS event received and processing"}
    except Exception as e:
        logger.error(f"Error processing SMS webhook: {str(e)}", exc_info=True)
        # Include more details in the error response for debugging
        return {
            "status": "error", 
            "message": str(e),
            "payload_type": type(payload).__name__ if 'payload' in locals() else 'unknown',
            "event_type": payload.get("eventType", "unknown") if 'payload' in locals() and not isinstance(payload, list) else "batch"
        }

def main():
    """Run the FastAPI app with Uvicorn"""
    # Log environment variables (excluding sensitive data)
    logger.info(f"Running with Azure Communication Service endpoint: {os.environ.get('AZURE_COMMUNICATION_SERVICE_ENDPOINT', 'Not set')}")
    logger.info(f"Connection string configured: {'Yes' if os.environ.get('AZURE_COMMUNICATION_SERVICE_CONNECTION_STRING') else 'No'}")
    logger.info(f"Azure OpenAI endpoint configured: {'Yes' if os.environ.get('AZURE_OPENAI_ENDPOINT') else 'No'}")
    logger.info(f"Azure OpenAI key configured: {'Yes' if os.environ.get('AZURE_OPENAI_KEY') else 'No'}")
    logger.info(f"Azure OpenAI model configured: {os.environ.get('AZURE_OPENAI_MODEL', 'Not set')}")
    logger.info(f"Phone number configured: {os.environ.get('PHONE_NUMBER', 'Not set')}")
    logger.info(f"Server configured to run on: {os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8000')}")
    
    # Get host and port from environment variables or use defaults
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    
    uvicorn.run("main:app", host=host, port=port, reload=True)

if __name__ == "__main__":
    main()
