# Import necessary libraries
from quart import Quart, request, jsonify, abort
import asyncio
from functools import partial
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from notion_client import AsyncClient
import datetime
from dotenv import load_dotenv
import os

# Load environment variables from a .env file for better security and configuration management
load_dotenv()

# Initialize Quart app
app = Quart(__name__)

# Initialize Notion client with an authentication token from environment variables
notion = AsyncClient(auth=os.getenv("NOTION_TOKEN"))

# Define the Google API scope needed for calendar access
SCOPES = ["https://www.googleapis.com/auth/calendar"]

async def run_in_executor(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    pfunc = partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, pfunc)

def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            flow.redirect_uri = "http://127.0.0.1:5000/"
            creds = flow.run_local_server(port=0)
            with open("token.json", "w") as token:
                token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)

@app.route("/read_events", methods=["GET"])
async def read_events():
    service = get_calendar_service()
    calendar_id = request.args.get("calendar_id", "primary")
    time_min = request.args.get("time_min", datetime.datetime.utcnow().isoformat() + "Z")
    time_max = request.args.get("time_max", None)

    events = []
    page_token = None

    try:
        while True:
            list_request = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
                maxResults=2500
            )
            events_result = await run_in_executor(list_request.execute)
            events.extend(events_result.get("items", []))
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break
        return jsonify(events)
    except Exception as e:
        abort(500, description=str(e))

@app.route("/create_event", methods=["POST"])
async def create_event():
    service = get_calendar_service()
    data = await request.get_json()

    calendar_id = data.get("calendar_id", "primary")
    summary = data.get("summary")
    description = data.get("description")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    attendees = data.get("attendees", [])

    if not all([summary, start_time, end_time]):
        abort(400, description="Missing required event fields.")

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
        "attendees": [{"email": attendee} for attendee in attendees],
    }

    try:
        insert_request = service.events().insert(calendarId=calendar_id, body=event)
        created_event = await run_in_executor(insert_request.execute)
        return jsonify(created_event)
    except Exception as e:
        abort(500, description=str(e))

@app.route("/edit_event", methods=["PUT"])
async def edit_event():
    service = get_calendar_service()
    data = await request.get_json()

    calendar_id = data.get("calendar_id", "primary")
    event_id = data.get("event_id")
    if not event_id:
        abort(400, description="Event ID is required.")

    try:
        get_request = service.events().get(calendarId=calendar_id, eventId=event_id)
        event = await run_in_executor(get_request.execute)
    except Exception as e:
        abort(500, description=f"Error retrieving event: {str(e)}")

    summary = data.get("summary")
    description = data.get("description")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    attendees = data.get("attendees")

    if summary is not None:
        event["summary"] = summary
    if description is not None:
        event["description"] = description
    if start_time is not None:
        event["start"] = {"dateTime": start_time}
    if end_time is not None:
        event["end"] = {"dateTime": end_time}
    if attendees is not None:
        event["attendees"] = [{"email": attendee} for attendee in attendees]

    try:
        update_request = service.events().update(calendarId=calendar_id, eventId=event_id, body=event)
        updated_event = await run_in_executor(update_request.execute)
        return jsonify(updated_event)
    except Exception as e:
        abort(500, description=f"Error updating event: {str(e)}")

@app.route("/delete_event", methods=["DELETE"])
async def delete_event():
    service = get_calendar_service()
    calendar_id = request.args.get("calendar_id", "primary")
    event_id = request.args.get("event_id")

    if not event_id:
        abort(400, description="Event ID is required.")

    try:
        delete_request = service.events().delete(calendarId=calendar_id, eventId=event_id)
        await run_in_executor(delete_request.execute)
        return jsonify({"status": "success", "message": "Event deleted successfully"})
    except Exception as e:
        abort(500, description=str(e))

@app.route("/list_notion_databases", methods=["GET"])
async def list_notion_databases():
    try:
        response = await notion.search(filter={"property": "object", "value": "database"})
        databases = response.get("results", [])
        databases_info = [
            {
                "id": db["id"],
                "title": db["title"][0]["plain_text"] if db.get("title") else "Unnamed Database",
            }
            for db in databases
        ]
        return jsonify(databases_info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/list_notion_pages", methods=["GET"])
async def list_notion_pages():
    try:
        response = await notion.search(filter={"property": "object", "value": "page"})
        pages = response.get("results", [])
        page_list = []
        for page in pages:
            title = "Unnamed Page"
            if "title" in page.get("properties", {}):
                try:
                    title = page["properties"]["title"]["title"][0]["plain_text"]
                except (IndexError, KeyError):
                    pass
            page_info = {
                "id": page["id"],
                "title": title,
                "created_time": page.get("created_time"),
                "last_edited_time": page.get("last_edited_time"),
                "url": page.get("url"),
            }
            page_list.append(page_info)
        return jsonify(page_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def retrieve_all_blocks(block_id):
    try:
        collected_blocks = []
        blocks_to_process = [block_id]

        while blocks_to_process:
            current_block_id = blocks_to_process.pop()
            response = await notion.blocks.children.list(block_id=current_block_id)
            block_children = response.get("results", [])

            for block in block_children:
                collected_blocks.append(block)
                if block.get("has_children", False):
                    blocks_to_process.append(block["id"])

        return collected_blocks
    except Exception as e:
        return str(e)

def extract_text_from_blocks(blocks):
    text_content = []

    for block in blocks:
        block_type = block.get("type")
        block_data = block.get(block_type, {})

        if block_type in [
            "paragraph",
            "heading_1",
            "heading_2",
            "heading_3",
            "bulleted_list_item",
            "numbered_list_item",
            "to_do",
        ]:
            text_elements = block_data.get("rich_text", [])
            text_pieces = [element.get("plain_text", "") for element in text_elements]
            if block_type in ["heading_1", "heading_2", "heading_3"]:
                text_content.append("**" + "".join(text_pieces) + "**")
            else:
                text_content.append("".join(text_pieces))

        elif block_type == "child_page":
            page_title = block_data.get("title", "")
            text_content.append(page_title)

    return text_content

async def get_all_text_on_page(page_id):
    blocks = await retrieve_all_blocks(page_id)
    return extract_text_from_blocks(blocks)

@app.route("/get_text_from_notion_page", methods=["GET"])
async def get_text_from_notion_page():
    page_id = request.args.get("page_id")
    if not page_id:
        return jsonify({"error": "Page ID is required"}), 400

    try:
        all_text = await get_all_text_on_page(page_id)
        return jsonify({"page_id": page_id, "content": all_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_notion_database_pages", methods=["GET"])
async def get_notion_database_pages():
    database_id = request.args.get("database_id")
    if not database_id:
        return jsonify({"error": "Database ID is required"}), 400

    try:
        pages = await query_database(database_id)
        return jsonify(pages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def get_database_schema(database_id):
    try:
        return await notion.databases.retrieve(database_id=database_id)
    except Exception as e:
        return str(e)

@app.route("/get_notion_database_schema", methods=["GET"])
async def get_notion_database_schema():
    database_id = request.args.get("database_id")
    if not database_id:
        return jsonify({"error": "Database ID is required"}), 400

    try:
        database_schema = await get_database_schema(database_id)
        return jsonify(database_schema)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/update_notion_database_entry", methods=["POST"])
async def update_notion_database_entry():
    data = await request.get_json()
    page_id = data.get("page_id")
    updated_properties = data.get("updated_properties")

    if not page_id or not updated_properties:
        return jsonify({"error": "Page ID and updated properties are required"}), 400

    try:
        result = await notion.pages.update(page_id=page_id, properties=updated_properties)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/create_notion_entry", methods=["POST"])
async def create_notion_entry():
    data = await request.get_json()
    database_id = data.get("database_id")
    properties = data.get("properties")
    content = data.get("content")

    if not database_id or not properties:
        return jsonify({"error": "Database ID and properties are required"}), 400

    new_page = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    if content:
        new_page["children"] = content

    try:
        response = await notion.pages.create(**new_page)
        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/query_notion_database", methods=["POST"])
async def query_notion_database():
    data = await request.get_json()
    database_id = data.get("database_id")
    filter = data.get("filter")
    sorts = data.get("sorts")
    page_size = data.get("page_size", 100)

    if not database_id:
        return jsonify({"error": "Database ID is required"}), 400

    results = []
    start_cursor = None

    try:
        while True:
            response = await notion.databases.query(
                database_id=database_id,
                filter=filter,
                sorts=sorts,
                start_cursor=start_cursor,
                page_size=page_size
            )
            results.extend(response.get("results", []))
            has_more = response.get("has_more")
            start_cursor = response.get("next_cursor")
            if not has_more:
                break

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run()