import logging
from typing import Dict, Any
from file_processor import validity_check, dict_maker # <-- import custom functions

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NodeCounter:
    """Class to handle XML validation and node counting using external logic"""

    def count_nodes(self, xml_content: str) -> Dict[str, Any]:
        """
        Count nodes using custom node counter
        and validate using external validator
        """
        try:
            # Step 1: Validate XML structure
            is_valid, errors = validity_check(xml_content)
            logger.info(f"Return error: {is_valid}")
            if not is_valid:
                return {
                    "success": False,
                    "error": errors[0] if errors else "Invalid XML structure",
                    "node_count": 0
                }

            # Step 2: Sanity check for <entity> names
            try:
                logger.info("try:xml_sanity_check")
                xml_sanity_check(xml_content)
            except ValueError as ve:
                logger.info("Error sanity check")
                return {
                    "success": False,
                    "error": str(ve),
                    "node_count": 0
                }

            # Step 3: Count nodes

            node_dict = dict_maker(xml_content)
            node_count = len(node_dict)
            logger.info(f"Actual node count: {node_count}")

            if node_count == 0:
                return {
                    "success": False,
                    "error": "No nodes found in XML.",
                    "node_count": 0
                }

            logger.info(f"Node count: {node_count}")

            return {
                "success": True,
                "node_count": node_count,
                "validated_xml": xml_content
            }

        except Exception as e:
            logger.error(f"Node counting error: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to analyze XML: {str(e)}",
                "node_count": 0
            }

def count_xml_nodes(xml_content: str) -> Dict[str, Any]:
    counter = NodeCounter()
    return counter.count_nodes(xml_content)



import defusedxml.ElementTree as ET
def xml_sanity_check(xml_content: str):
    """
    Validate <entity> values in XML.
    Raise error if an entity ends with '#' or '#/' (and similar cases).
    Return list of valid entities.
    """
    root = ET.fromstring(xml_content)
    valid_entities = []

    for entity in root.findall(".//entity"):
        text = (entity.text or "").strip()

        # Sanity check
        if text.endswith("#") or text.endswith("#/"):
            raise ValueError(f"Invalid entity name found: {text}")

        valid_entities.append(text)

    return valid_entities