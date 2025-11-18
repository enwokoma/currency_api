
import json

def beautify_webhook_logs(file_path):
    with open(file_path, 'r') as f:
        content = f.read()

    # The file contains concatenated JSON objects. We need to make it a valid JSON array.
    # Each JSON object is on a new line.
    json_strings = content.strip().split('\n')
    
    parsed_objects = []
    for json_str in json_strings:
        try:
            obj = json.loads(json_str)
            # The 'body' is a JSON string, so we parse it as well for beautification.
            if 'body' in obj and isinstance(obj['body'], str):
                obj['body'] = json.loads(obj['body'])
            parsed_objects.append(obj)
        except json.JSONDecodeError as e:
            print(f"Skipping invalid JSON object: {json_str} - Error: {e}")


    with open(file_path, 'w') as f:
        json.dump(parsed_objects, f, indent=4)

if __name__ == '__main__':
    # Store the webhook notification logs in a JSON file
    beautify_webhook_logs('webhook_logs.json')
