# OpenAI SMS Sample

## Overview

OpenAI SMS Sample is a system designed to automate and streamline the initial tenant screening process using SMS interactions and AI-powered conversations. The system collects essential information from prospective tenants through an SMS-based form and provides a web interface for document uploads.

## System Architecture

### Core Components

1. **SMS Receiver API**: A FastAPI-based webhook endpoint that receives and processes SMS events from Azure Communication Services.
2. **AI Conversational Agent**: Azure OpenAI integration that generates responses to user messages while maintaining conversation context.
3. **File Upload System**: A component to manage ID photo uploads from prospective tenants.
4. **Conversation Management System**: A system to track and manage conversation history with users.

## Functional Requirements

### SMS Processing

1. **Webhook Integration**
   - Receive webhook notifications from Azure Communication Services
   - Process SMS delivery reports
   - Handle Azure Event Grid subscription validation automatically
   - Support both single event and batch event delivery formats

2. **Message Reception**
   - Process incoming SMS messages from prospective tenants
   - Log all received SMS events with relevant details

3. **Message Sending**
   - Send AI-generated responses back to users via SMS
   - Enable delivery reports for sent messages
   - Handle and log any sending errors

### AI Conversation Flow

1. **Form Data Collection**
   - Collect the following information from users in a conversational manner:
     - First Name
     - Last Name
     - Phone Number (with option to confirm current or provide alternate)
     - Address
     - Annual Income
   - Confirm each piece of information before proceeding to the next

2. **Conversation Management**
   - Maintain conversation context across multiple SMS exchanges
   - Store conversation history for each user (keyed by phone number)
   - Provide endpoints to view, manage, and delete conversation history
   - Ensure AI responses are concise and suitable for SMS format (limited to 100 tokens)

3. **System Prompting**
   - Use specific system prompt to guide the AI in collecting form data
   - Ensure polite, professional, and clear communication
   - Verify information with users before proceeding

### File Upload System

1. **ID Photo Upload Interface**
   - Provide a mobile-friendly web interface for ID photo uploads
   - Store uploaded files securely in a dedicated directory
   - Associate uploads with the corresponding user account

### API Endpoints

1. **Health Monitoring**
   - `GET /`: Health check endpoint to verify system operation

2. **SMS Webhook**
   - `POST /api/sms/webhook`: Primary webhook endpoint for Azure Communication Services
   - Process validation, incoming messages, and delivery reports

3. **Conversation Management**
   - `GET /api/conversations`: List all phone numbers with active conversations
   - `GET /api/conversations/{phone_number}`: Get conversation history for a specific user
   - `DELETE /api/conversations/{phone_number}`: Delete conversation history for a specific user

## Technical Requirements

### Environment Configuration

1. **Required Environment Variables**
   - `AZURE_COMMUNICATION_SERVICE_CONNECTION_STRING` or `AZURE_COMMUNICATION_SERVICE_ENDPOINT`: For Azure Communication Services authentication
   - `AZURE_OPENAI_ENDPOINT`: Azure OpenAI service endpoint
   - `AZURE_OPENAI_KEY`: API key for Azure OpenAI service
   - `AZURE_OPENAI_MODEL`: Deployed model name for generation
   - `PHONE_NUMBER`: The Azure Communication Services phone number for sending SMS
   - `HOST`: Host address (default: 0.0.0.0)
   - `PORT`: Port number (default: 8000)

2. **Authentication Methods**
   - Support for connection string or managed identity credentials for Azure Communication Services

### Performance and Reliability

1. **Background Task Processing**
   - Process webhook events in background tasks to minimize response time
   - Ensure proper error handling and logging for background processes

2. **Error Handling**
   - Comprehensive error handling for all API endpoints
   - Detailed logging for troubleshooting
   - Graceful degradation when services are unavailable

### Security

1. **Data Protection**
   - Secure storage of user conversations and uploaded files
   - Protection of sensitive environment variables
   - Proper validation of incoming requests

## Development and Deployment

### Local Development

1. **Development Environment**
   - Python 3.13 or higher required
   - FastAPI development server with hot reloading

2. **Local Testing**
   - Azure Dev Tunnels for exposing local server to the internet
   - Testing webhooks locally with real Azure services

### Deployment

1. **Environment Setup**
   - Configuration of all required environment variables
   - Setting up Azure Communication Services Event Grid subscription
   - Configuring Azure OpenAI service with appropriate model

2. **Runtime Requirements**
   - Python 3.13+ runtime
   - Exposure of port 8000 (or configured port)
   - Public network access for webhook endpoint

## Future Enhancements

1. **Function Call Implementation**
   - Add function call capability when the form is complete with all required data
   - Trigger backend processes when data collection is complete

2. **Enhanced Web Interface**
   - Improve the mobile website for ID photo uploads
   - Add authentication for increased security
   - Provide status tracking for applications

3. **Data Integration**
   - Connect with property management systems
   - Integrate with background check services
   - Support for document verification systems
