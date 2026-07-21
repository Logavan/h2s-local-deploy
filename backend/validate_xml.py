import sys
import json
import defusedxml.ElementTree as ET
from xml.parsers.expat import ExpatError

def validate_xml_content(xml_content, file_type):
    """
    Validates XML content and returns validation results
    
    Args:
        xml_content (str): The XML content to validate
        file_type (str): The type of XML file
        
    Returns:
        dict: Validation results
    """
    try:
        # Try to parse the XML
        root = ET.fromstring(xml_content)
        
        # Basic validation passed if we get here
        result = {
            "valid": True,
            "message": "XML is valid",
            "fileType": file_type
        }
        
        # Add more specific validation based on file_type if needed
        if file_type == "hana":
            # Specific validation for HANA XML files
            # Example: Check for required elements
            required_elements = ["table", "column"]
            missing_elements = [elem for elem in required_elements if root.find(f".//{elem}") is None]
            
            if missing_elements:
                result["valid"] = False
                result["message"] = f"Missing required elements: {', '.join(missing_elements)}"
        
        return result
    except ExpatError as e:
        # XML parsing error
        return {
            "valid": False,
            "message": f"XML parsing error: {str(e)}",
            "fileType": file_type
        }
    except Exception as e:
        # Other errors
        return {
            "valid": False,
            "message": f"Validation error: {str(e)}",
            "fileType": file_type
        }

# This allows the script to be run directly or imported as a module
if __name__ == "__main__":
    # Read input from stdin when run as a script
    xml_content = sys.stdin.read()
    file_type = "hana"  # Default file type
    
    result = validate_xml_content(xml_content, file_type)
    print(json.dumps(result))
