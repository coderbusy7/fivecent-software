Here is the comprehensive feature set and technical architecture for the Antibes yacht brokerage WhatsApp AI agent platform.

---

### Core System Features

#### 1. Inbound Qualification & Charter Extraction

* **Natural Language Parameter Extraction:** Parses casual texts (e.g., *"Need a 20m boat this Saturday out of Cannes"*) into structured parameters: dates, duration, vessel length/category, embarkation port, and guest count.
* **Maritime Safety Enforcement:** Hard-coded rule enforcing the standard French/EU commercial maritime safety limit of **maximum 12 cruising passengers** on day charters. Flags group sizes over 12 immediately to suggest tandem charters or commercially coded mini-cruise vessels.
* **Missing Field Proactive Follow-Up:** Gathers itinerary objectives (e.g., Lérins Islands vs. Saint-Tropez run) and port preferences without bombarding the client.

#### 2. Deterministic Mediterranean Quoting & Accounting

* **Statutory French VAT Handling:** Enforces the mandatory **20% French VAT** on charter and skipper services.
* **APA (Advance Provisioning Allowance) Calculation:** Calculates the standard **30% APA fund** (escrow for marine fuel, berthing fees, and custom provisioning), explaining clearly that any surplus is refunded at checkout.
* **Seasonal Price Switching:** Applies July/August high-season tariffs versus May/June/September/October low-season tariffs automatically based on the target calendar date.
* **Itemized Transparency:** Delivers complete financial breakdowns (Base + Skipper + VAT + APA = Total) to eliminate surprise dock surcharges.

#### 3. Bounded Negotiation & Discount Control

* **Zero-Hallucination Pricing Guard:** Prevents the LLM from inventing arbitrary numbers or unapproved discounts.
* **Statutory Expense Protection:** Treats VAT and APA as strictly non-negotiable costs.
* **Margin Boundary Check:** Restricts concessions to a hard ceiling (e.g., max 8% broker base concession). If the client bids below the floor, the agent pivots to high-perceived-value perks (e.g., complimentary Laurent-Perrier on board) or escalates the offer directly to the broker.

#### 4. Broker Visibility & Circuit-Breaker Takeover

* **24-Hour Soft-Hold Reservation:** Automatically places a temporary 24-hour option on the requested boat upon receiving the lead's name and email.
* **Real-Time Shadow Alerts:** Pushes an instant summary card to the broker's personal WhatsApp or Telegram channel whenever a lead requests an option or pushes below pricing bounds.
* **Human-in-the-Loop Circuit Breaker (`is_human_active`):** The instant a human broker sends a manual message from WhatsApp Web or their phone app, the AI agent is muted for 24 hours. The bot never talks over a human.

#### 5. Fleet Inventory Management

* **Zero-Frontend Config:** Brokers can edit boat rates, high/low season parameters, or mark boats inactive directly in a shared Google Sheet or JSON registry.
* **Multi-Tenant Scoping:** Separates fleet catalogs, phone numbers, and conversation histories cleanly by agency identifier (`tenant_id`).

---

### System Architecture

The platform follows a decoupled, 4-tier modular pipeline designed for low latency, zero state corruption, and testability.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        1. INGESTION & TRANSPORT                        │
│                                                                        │
│   Meta WhatsApp Cloud API                    Direct Curl / Testing     │
│   [POST /webhook]                            [POST /test-chat]         │
│          │                                          │                  │
│          ▼ (<50ms ACK: HTTP 200 OK)                 ▼                  │
│   FastAPI BackgroundTasks ──────────────────────────┤                  │
└─────────────────────────────────────────────────────┼──────────────────┘
                                                      │
                                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   2. STATE & HUMAN-IN-THE-LOOP CONTROL                 │
│                                                                        │
│   Conversation Ledger (PostgreSQL / Redis / Memory):                   │
│   • Multi-turn session message history                                 │
│   • Lead state (Name, Email, Pax, Port, Date)                          │
│   • Circuit Breaker Flag (`is_human_active: bool`)                     │
│                                                                        │
│   [Check: Is Human Broker Active?] ──► YES ──► Mute Agent & Abort      │
│                 │                                                      │
│                 ▼ NO                                                   │
└─────────────────┼──────────────────────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  3. ORCHESTRATION & REASONING CORE                     │
│                                                                        │
│   OpenAI gpt-4o-mini (Native Function Calling)                         │
│   System Persona: "Elena, Executive Concierge at Port Vauban"          │
│                                                                        │
│   Decides: Normal Chat Reply vs. Execute Deterministic Tool Call       │
└─────────────────┬──────────────────────────────────▲───────────────────┘
                  │                                  │
                  │ Tool Call Request                │ Tool Result JSON
                  ▼                                  │
┌────────────────────────────────────────────────────┴───────────────────┐
│                 4. DETERMINISTIC TOOLS & FLEET DOMAIN                  │
│                                                                        │
│   • check_fleet_inventory(length_m, pax, date)                         │
│   • calculate_charter_quote(boat_name, days, skipper=True)             │
│       └─ Base + 20% French VAT + 30% APA + Skipper                     │
│   • evaluate_negotiation(boat_name, proposed_budget, original_total)   │
│   • escalate_and_hold(customer_name, email, boat, date, quote)         │
│                                                                        │
│   Data Source: In-Memory DB / Google Sheets API / PostgreSQL           │
└─────────────────┬──────────────────────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     5. NOTIFICATION & DISPATCH                         │
│                                                                        │
│   • Outbound Client Reply via Meta Graph API                           │
│   • Instant Broker Alert via WhatsApp / Telegram / Chatwoot            │
└────────────────────────────────────────────────────────────────────────┘

```

---

### Layer-by-Layer Architectural Breakdown

| Architectural Layer | Technical Component | Primary Responsibility |
| --- | --- | --- |
| **1. Ingestion Layer** | FastAPI (`GET /webhook`, `POST /webhook`, `POST /test-chat`) | Handles Meta's webhook verification handshake, decouples slow LLM operations from Meta's strict 50ms HTTP 200 response requirement using `BackgroundTasks`. |
| **2. State & Guardrails Layer** | Session Store (`dict` / Redis / PostgreSQL) | Stores conversation turns by phone number, extracts contact info, and enforces the `is_human_active` kill switch. |
| **3. Orchestration Layer** | OpenAI SDK (`gpt-4o-mini`) + System Prompt | Enforces brand voice, guides the multi-turn dialogue, parses user intent, and maps requests to specific tool schemas. |
| **4. Domain Tools Layer** | Pure Python Functions (`math`, `datetime`, `json`) | Performs all financial and inventory operations deterministically without LLM approximation. |
| **5. Integration Layer** | Meta Graph API REST Client + Telegram Bot API | Dispatches formatted outbound WhatsApp messages and alerts the broker's phone with ready-to-close lead cards. |



# WhatsApp Cloud API Get Started


**Warning:** This documentation is for developers building on the WhatsApp Business Platform. If you are a WhatsApp user experiencing issues with your personal account, visit the [WhatsApp Help Center](https://faq.whatsapp.com/) for support.

This guide helps developers quickly get started with the WhatsApp Cloud API. It covers the basic setup steps, including registering as a developer, creating a Meta app, sending your first message, and setting up a test webhook endpoint. You'll also learn how to generate secure access tokens and send both template and non-template messages. Advanced features and further resources are introduced for deeper exploration.

---

## Download the sample app

The Jasper's Market sample app contains all of the messages and code used in the Jasper's Market demo. You can use this sample app to learn how to build an application that sends and handles WhatsApp Cloud API data.

[Download the Jasper's Market Sample App](https://github.com/fbsamples/whatsapp-business-jaspers-market)

---

## Prerequisites

- Facebook account or managed Meta account
- Developer registration
  - If not yet registered, visit the [developer registration page](https://developers.facebook.com/async/registration/) and follow the prompts.
- WhatsApp-enabled device for sending and receiving test messages

---

## Step 1. Create a new Meta app with WhatsApp

1. Open the [Meta App Dashboard](https://developers.facebook.com/apps) to create a new Meta app with the WhatsApp use case.
2. Click **Create App**.
3. Add your app's name and your email.
4. Select the **Connect with customers through WhatsApp** use case and click **Next**.
5. Select an existing business portfolio or create a new one.
6. A list of publishing requirements are listed. You may not have any at this point. Click **Next**.
7. Confirm your details, use case, and business portfolio. Click **Previous** to make changes or **Create app** to complete app creation.

After creating your app with the WhatsApp use case you are redirected to the **Customize use case > Connect on WhatsApp > Quickstart** page in the dashboard.

## Step 2. Start using the API

1. Click the **Start using the API** button to set up the API by adding a phone number and sending your first message. You are redirected to the **API Setup** page.
2. In the **API Setup** section, connect your app to a WhatsApp Business account. This connection allows your app to access the WhatsApp Cloud API and send messages on behalf of your business. Select an existing WhatsApp Business account or create a new one:
   - **To use an existing account:** Select the WhatsApp Business account from the dropdown menu.
   - **To create a new account:** Click **Create a WhatsApp Business account** and follow the prompts to set up your business profile.
5. Once connected, you will see your WhatsApp Business account ID displayed in the API Setup panel.
   - Save this ID for use in API calls.


> **Note:** If you created a new Meta Business Portfolio during app creation, a WhatsApp Business account may have been automatically created for you. Verify the connection in the API Setup section before proceeding.

---

## Step 3. Send and receive messages

1. Click **Generate access token** to generate a temporary access token to send a test message.
2. Select a **From** phone number, or add a new one, from the dropdown menu.
3. Add a **To** phone number that will receive the test message.
4. Click the **Send message** button to send your first message.
    - Make sure to retain both your test phone number ID and WhatsApp Business account ID for later use.
4. Once you receive the message you sent, make sure to reply back to keep the conversation going.

The left-side menu lists ways in which you can customize use case settings and permissions to make your app work the way you want it to. You can update these settings at any time.

- [**Permissions and features**](#permissions-and-features) - View required and optional permissions for this use case and add them to an App Review submission, if applicable.
- [**Quickstart**](#quickstart) - Start using the API and learn how to scale your business, improve ROI, and manage your WhatsApp Business account.
- [**API Setup**](#api-setup) - Generate access tokens, send and receive messages, and configure webhooks and the WhatsApp SDK.
- [**Configuration**](#configuration) - Configure webhooks and the WhatsApp SDK.
- [**Resources**](#resources) – View the WhatsApp developer documentation, Meta Blueprint courses, and support resources.
- [**Tech Provider onboarding**](#tech-provider-onboarding) – Start scaling the WhatsApp Business Platform for your business.
- [**Partner Solutions**](#partner-solutions) – Create a partner solution.
- [**Embedded Signup Builder**](#embedded-signup-builder) – Integrate Embedded Signup flow into your website or client portal.

---

## Step 4. Set up the test webhook app

You will need to set up a webhook endpoint in order to receive notifications about message statuses, such as read and delivered.

Use the sample webhook server for testing purposes by following the [Using a test webhook app](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/set-up-whatsapp-echo-bot) guide.

Once your test webhook application is established, respond in the WhatsApp chat thread you created with yourself. You will see the webhook payload in your test application like this:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "215589313241560883",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15551797781",
              "phone_number_id": "7794189252778687"
            },
            "contacts": [
              {
                "profile": {
                  "name": "Jessica Laverdetman"
                },
                "wa_id": "13557825698"
              }
            ],
            "messages": [
              {
                "from": "17863559966",
                "id": "wamid.HBgLMTc4NjM1NTk5NjYVAGHAYWYET688aASGNTI1QzZFQjhEMDk2QQA=",
                "timestamp": "1758254144",
                "text": {
                  "body": "Hi!"
                },
                "type": "text"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

---

## Step 5. Create a system user and generate a permanent access token

The temporary access token you created to send the `hello_world` template message expires quickly and is not suitable for development purposes.
So you should create a permanent token for use across the WhatsApp Business Platform.

1. Navigate to [Business Settings](https://business.facebook.com/latest/settings) and click **System users** in the sidebar.
2. Click the **Add+** button in the upper-right corner and follow the prompts to create a new system user.
3. Select the new system user you created, and click **Assign Assets.**
   - Select your app and toggle **Manage app** under **Full control.**
   - Select your WhatsApp account and toggle **Manage WhatsApp Business accounts** under **Full control.**
   - Click the **Assign assets** button.
4. Click **Generate token.**
   - Follow the prompts to generate your token.
   - Add the following permissions to the token:
     - [business_management](https://developers.facebook.com/docs/permissions#b)
     - [whatsapp_business_messaging](https://developers.facebook.com/docs/permissions#w)
     - [whatsapp_business_management](https://developers.facebook.com/docs/permissions#w)
   - Copy the token and store it in a secure place to be used in the later steps.

---

## Step 6. Send a non-template message

When you responded to your earlier test message, you triggered what is known as a [customer service window](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#customer-service-windows). This 24-hour window allows you to send [non-template messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#message-types) to users on WhatsApp. With the customer service window now open, you can send a non-template message to yourself.
To do this, insert your test phone number ID, the system user access token, and your phone number in the code sample below, then paste the code into your terminal and run it.

```bash
curl 'https://graph.facebook.com/v23.0/<TEST_BUSINESS_PHONE_NUMBER_ID>/messages' \
-H 'Content-Type: application/json' \
-H 'Authorization: Bearer <SYSTEM_USER_ACCESS_TOKEN>' \
-d '
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "<WHATSAPP_USER_PHONE_NUMBER>",
  "type": "text",
  "text": {
    "body": "Hello!"
  }
}'
```

After successfully sending your message, check your test webhook application to view the webhook event confirming the message receipt.

---

## Step 7. Finish

The WhatsApp Cloud API enables you to send messages and receive webhooks—these are the fundamental building blocks for messaging integration.
Beyond these basics, the API offers additional features such as group creation and management, as well as support for calling.
To explore these advanced capabilities, check out the "Learn more" section below.

---

## Learn more

- [Learn about the different types of non-template messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [Learn how to create and send template messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview)
- [Learn how to create and manage WhatsApp groups via API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups)
- [Learn how to send and receive calls on WhatsApp via API](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling)
- [Learn how to add a business phone number](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers)
- [Learn how to set up your own webhook server](https://developers.facebook.com/docs/graph-api/webhooks/getting-started)
- [Onboard WhatsApp Business app users](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users) — allow businesses already using the WhatsApp Business app to connect their existing account and phone number to Cloud API via Embedded Signup
- [Become a partner](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/overview)
- [View WhatsApp API OpenAPI Specification](https://github.com/facebook/openapi)
