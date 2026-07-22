"""
Node Cache Module
Provides caching for node dictionaries using pickle files
"""
import os
import pickle
import tempfile

# Use a temporary directory for pickle cache files
CACHE_DIR = os.path.join(tempfile.gettempdir(), 'hana_cv_node_cache')
os.makedirs(CACHE_DIR, exist_ok=True)


def get_pickle_path(task_id):
    """Returns the file path for a given task ID's pickle file."""
    return os.path.join(CACHE_DIR, f"{task_id}.pkl")


def save_node_dict(task_id, node_dict):
    """
    Saves a node dictionary to a pickle file.
    """
    try:
        path = get_pickle_path(task_id)
        with open(path, 'wb') as f:
            pickle.dump(node_dict, f)
    except Exception as e:
        print(f"[node_cache] Failed to save node_dict for {task_id}: {e}")


def load_node_dict(task_id):
    """
    Loads a node dictionary from a pickle file.
    Returns None if the file doesn't exist or fails to load.
    """
    try:
        path = get_pickle_path(task_id)
        if not os.path.exists(path):
            return None
        with open(path, 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        print(f"[node_cache] Failed to load node_dict for {task_id}: {e}")
        return None


def delete_node_dict_pickle(task_id):
    """
    Deletes the pickle file for a given task ID.
    """
    try:
        path = get_pickle_path(task_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[node_cache] Failed to delete node_dict for {task_id}: {e}")