import logging
from typing import Dict, Any
from file_processor import validity_check, dict_maker # <-- import custom functions

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NodeCounter:
    """Class to handle XML validation and node counting using external logic"""

    def count_nodes(self, xml_content: str, daily_free_conversions_used: int) -> Dict[str, Any]:
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

            # Step 4: Complexity & Cost
            credit_cost = 0
            conversion_type = "Free"
            complexity = "unknown"
            FREE_CONVERSION_LIMIT_PER_DAY = 5

            if 1 <= node_count <= 10:
                # Check if user still has free conversions available
                if daily_free_conversions_used < FREE_CONVERSION_LIMIT_PER_DAY:
                    credit_cost = 0
                    conversion_type = "Free"
                else:
                    # Daily free limit exhausted - make it Paid
                    credit_cost = 10
                    conversion_type = "Paid"
                complexity = "low"
            elif 10 < node_count <= 20:
                credit_cost = 10
                conversion_type = "Paid"
                complexity = "medium"
            elif 20 < node_count <= 40:
                credit_cost = 20
                conversion_type = "Paid"
                complexity = "high"
            elif node_count > 40:
                credit_cost = 30
                conversion_type = "Paid"
                complexity = "very_high"

            logger.info(f"Node count: {node_count}, Type: {conversion_type}, Cost: {credit_cost} credits, Daily Free Used: {daily_free_conversions_used}")

            return {
                "success": True,
                "node_count": node_count,
                "conversion_type": conversion_type,
                "credit_cost": credit_cost,
                "validated_xml": xml_content,
                "complexity": complexity
            }

        except Exception as e:
            logger.error(f"Node counting error: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to analyze XML: {str(e)}",
                "node_count": 0
            }

def count_xml_nodes(xml_content: str, daily_free_conversions_used: int = 0) -> Dict[str, Any]:
    counter = NodeCounter()
    return counter.count_nodes(xml_content, daily_free_conversions_used)



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