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
from datetime import datetime, timedelta, time
import random
import json

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Appointment Reminder & Scheduling API")

# Set up directory for file uploads
UPLOAD_DIR = Path("uploads")
if not UPLOAD_DIR.exists():
    UPLOAD_DIR.mkdir(parents=True)

# In-memory conversation history storage
# Key: phone number, Value: list of message objects
conversation_history = {}

# Mock appointment data storage
# Key: phone number, Value: appointment info
customer_appointments = {}

# Mock calendar storage - 30 days of appointments
# Key: date string (YYYY-MM-DD), Value: list of time slots (HH:MM format)
mock_calendar = {}

def generate_mock_calendar():
    """Generate a 30-day mock calendar with 80% occupancy"""
    global mock_calendar
    
    # Business hours: 9 AM to 5 PM (30-minute slots)
    business_start = time(9, 0)
    business_end = time(17, 0)
    
    # Generate time slots (30-minute intervals)
    time_slots = []
    current_time = datetime.combine(datetime.today(), business_start)
    end_time = datetime.combine(datetime.today(), business_end)
    
    while current_time < end_time:
        time_slots.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=30)
    
    # Generate 30 days of calendar data (weekdays only)
    start_date = datetime.now().date()
    for i in range(30):
        current_date = start_date + timedelta(days=i)
        
        # Skip weekends
        if current_date.weekday() >= 5:  # Saturday = 5, Sunday = 6
            continue
            
        date_str = current_date.strftime("%Y-%m-%d")
        
        # Randomly book 80% of slots
        available_slots = time_slots.copy()
        num_to_book = int(len(time_slots) * 0.8)
        booked_slots = random.sample(available_slots, num_to_book)
        
        # Store available slots (not booked ones)
        mock_calendar[date_str] = [slot for slot in time_slots if slot not in booked_slots]
    
    logger.info(f"Generated mock calendar for {len(mock_calendar)} business days")

def get_customer_appointment(phone_number: str):
    """Get or create a mock appointment for a customer"""
    if phone_number not in customer_appointments:
        # Create a mock appointment for tomorrow
        tomorrow = datetime.now().date() + timedelta(days=1)
        
        # Skip weekend
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        
        # Random time slot
        time_slots = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", 
                     "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]
        appointment_time = random.choice(time_slots)
        
        customer_appointments[phone_number] = {
            "date": tomorrow.strftime("%Y-%m-%d"),
            "time": appointment_time,
            "type": "consultation",
            "status": "scheduled"
        }
    
    return customer_appointments[phone_number]

def get_available_slots(date_str: str, num_slots: int = 3):
    """Get available time slots for a given date"""
    if date_str in mock_calendar:
        available = mock_calendar[date_str]
        return random.sample(available, min(num_slots, len(available)))
    return []

def book_appointment(phone_number: str, date_str: str, time_slot: str):
    """Book an appointment and update the calendar"""
    if date_str in mock_calendar and time_slot in mock_calendar[date_str]:
        # Remove the slot from available slots
        mock_calendar[date_str].remove(time_slot)
        
        # Update customer appointment
        customer_appointments[phone_number] = {
            "date": date_str,
            "time": time_slot,
            "type": "consultation",
            "status": "scheduled"
        }
        return True
    return False

def format_date_friendly(date_str: str):
    """Convert YYYY-MM-DD to friendly format"""
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    return date_obj.strftime("%A, %B %d")

def format_time_friendly(time_str: str):
    """Convert HH:MM to friendly format"""
    time_obj = datetime.strptime(time_str, "%H:%M")
    return time_obj.strftime("%I:%M %p").lstrip('0')

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
                    
                    # Get customer's current appointment
                    current_appointment = get_customer_appointment(sender)
                    appointment_date_friendly = format_date_friendly(current_appointment["date"])
                    appointment_time_friendly = format_time_friendly(current_appointment["time"])
                    
                    # Get or initialize conversation history for this sender
                    if sender not in conversation_history:
                        # First message - send appointment reminder
                        conversation_history[sender] = [
                            {"role": "system", "content": f"""
You are a friendly and professional virtual receptionist for a medical practice. A customer has just texted, and this is their first interaction.

CUSTOMER'S CURRENT APPOINTMENT:
- Date: {appointment_date_friendly}
- Time: {appointment_time_friendly}
- Type: Consultation

YOUR TASKS:
1. FIRST: Greet them and remind them of their upcoming consultation appointment (date and time above)
2. Ask if they can confirm they'll be able to make it, or if they need to reschedule
3. If they want to reschedule:
   - Ask what date works better for them
   - Offer 2-3 available time slots for their preferred date
   - Confirm the new appointment once they choose
4. If they confirm the existing appointment, thank them and remind them to arrive 15 minutes early

IMPORTANT GUIDELINES:
- Be warm, professional, and helpful like a human receptionist
- Keep messages concise but friendly
- Always confirm appointment details clearly
- Business hours are 9 AM to 5 PM, Monday through Friday
- All appointments are 30-minute consultations
- If they ask for a date that's not available or outside business hours, politely suggest alternatives

Current date context: Today is {datetime.now().strftime('%A, %B %d, %Y')}
"""}
                        ]
                        
                        # Add a system message to start the conversation
                        initial_message = f"Hi! This is a reminder that you have a consultation scheduled for {appointment_date_friendly} at {appointment_time_friendly}. Can you confirm you'll be able to make it, or would you like to reschedule?"
                        
                        conversation_history[sender].append({"role": "assistant", "content": initial_message})
                        
                        # Send the initial reminder
                        await send_sms_response(sender, initial_message)
                        
                        # Don't process the user's message yet, just send the reminder
                        return
                    
                    # Add the user's message to the conversation history
                    conversation_history[sender].append({"role": "user", "content": message_content})
                    
                    # Add current calendar context to the system message
                    calendar_context = ""
                    if "reschedule" in message_content.lower() or "change" in message_content.lower():
                        # Get next few available dates
                        available_dates = []
                        current_date = datetime.now().date() + timedelta(days=1)
                        days_checked = 0
                        
                        while len(available_dates) < 5 and days_checked < 14:
                            if current_date.weekday() < 5:  # Weekday
                                date_str = current_date.strftime("%Y-%m-%d")
                                available_slots = get_available_slots(date_str, 3)
                                if available_slots:
                                    available_dates.append({
                                        "date": date_str,
                                        "friendly_date": format_date_friendly(date_str),
                                        "slots": available_slots
                                    })
                            current_date += timedelta(days=1)
                            days_checked += 1
                        
                        calendar_context = f"\n\nAVAILABLE APPOINTMENTS IN NEXT 2 WEEKS:\n"
                        for date_info in available_dates:
                            slots_friendly = [format_time_friendly(slot) for slot in date_info["slots"]]
                            calendar_context += f"- {date_info['friendly_date']}: {', '.join(slots_friendly)}\n"
                    
                    # Update system message with calendar context if needed
                    if calendar_context:
                        conversation_history[sender][0]["content"] += calendar_context
                    
                    logger.info(f"Conversation history for {sender}: {conversation_history[sender]}")
                    
                    # Generate a response using Azure OpenAI with conversation history
                    response = await client.chat.completions.create(
                        model=os.environ.get("AZURE_OPENAI_MODEL"),
                        messages=conversation_history[sender],
                        max_tokens=150
                    )
                    
                    # Extract the generated response
                    ai_response = response.choices[0].message.content.strip()
                    logger.info(f"Generated response: {ai_response}")
                    
                    # Add the assistant's response to the conversation history
                    conversation_history[sender].append({"role": "assistant", "content": ai_response})
                    
                    # Send the response back via SMS
                    await send_sms_response(sender, ai_response)
                    
                except Exception as openai_error:
                    logger.error(f"Error generating AI response: {str(openai_error)}", exc_info=True)
                
    except Exception as e:
        logger.error(f"Error processing SMS event: {str(e)}", exc_info=True)

async def send_sms_response(sender: str, message: str):
    """Helper function to send SMS response"""
    phone_number = os.environ.get("PHONE_NUMBER", "")
    logger.info(f"SMS Message Originator: {sender}")
    logger.info(f"AI Response: {message}")
    logger.info(f"Sending SMS from: {phone_number} to: {sender}")

    if not phone_number:
        logger.error("PHONE_NUMBER environment variable is not set")
        return

    sms_client = get_sms_client()
    if sms_client:
        try:
            logger.info(f"Attempting to send SMS response...")
            result = sms_client.send(
                from_=phone_number,  # Your ACS phone number
                to=[sender],
                message=message,
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

@app.get("/")
async def root():
    return {"message": "Appointment Reminder & Scheduling API is up and running"}

@app.get("/api/appointments")
async def get_appointments():
    """
    Get all customer appointments
    """
    return {"appointments": customer_appointments}

@app.get("/api/appointments/{phone_number}")
async def get_appointment(phone_number: str):
    """
    Get the appointment for a specific phone number
    """
    if phone_number in customer_appointments:
        return {"appointment": customer_appointments[phone_number]}
    else:
        return {"error": "No appointment found for this phone number"}

@app.get("/api/calendar")
async def get_calendar():
    """
    Get the mock calendar data
    """
    return {"calendar": mock_calendar}

@app.get("/api/calendar/{date}")
async def get_calendar_date(date: str):
    """
    Get available slots for a specific date (YYYY-MM-DD format)
    """
    if date in mock_calendar:
        return {"date": date, "available_slots": mock_calendar[date]}
    else:
        return {"error": "Date not found or not a business day"}

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
    # Generate mock calendar on startup
    generate_mock_calendar()
    
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
