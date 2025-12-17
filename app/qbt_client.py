import qbittorrentapi
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

def get_client(instance):
    """Creates and returns a qBittorrent client instance."""
    parsed_url = urlparse(instance.host)
    
    host = parsed_url.hostname
    port = parsed_url.port
    
    # Prepend scheme if it's missing, default to http
    scheme = parsed_url.scheme or 'http'
    full_host_url = f"{scheme}://{host}:{port}"

    try:
        client = qbittorrentapi.Client(
            host=full_host_url,
            username=instance.username,
            password=instance.password
        )
        return client
    except Exception as e:
        print(f"Failed to create qBittorrent client for {instance.name}: {e}")
        return None

def get_all_torrents(client, **kwargs):
    """
    Retrieves ALL torrents from qBittorrent with proper pagination handling.
    
    The qBittorrent API may have limits on how many torrents are returned per request.
    This function handles pagination to ensure all torrents are retrieved.
    
    Args:
        client: qBittorrent client instance
        **kwargs: Additional arguments to pass to torrents_info()
    
    Returns:
        List of all torrents
    """
    try:
        all_torrents = []
        limit = 1000  # Retrieve 1000 torrents at a time
        offset = 0
        
        while True:
            # Fetch batch of torrents with pagination
            batch = client.torrents_info(limit=limit, offset=offset, **kwargs)
            
            if not batch:
                # No more torrents to fetch
                break
            
            all_torrents.extend(batch)
            logger.debug(f"Retrieved {len(batch)} torrents (offset: {offset}, total so far: {len(all_torrents)})")
            
            # If we got fewer torrents than the limit, we've reached the end
            if len(batch) < limit:
                break
            
            offset += limit
        
        logger.info(f"Retrieved total of {len(all_torrents)} torrents")
        return all_torrents
    except Exception as e:
        logger.error(f"Error retrieving torrents with pagination: {e}")
        # Fallback to non-paginated call
        try:
            logger.warning("Falling back to non-paginated torrents_info() call")
            return client.torrents_info(**kwargs)
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            return [] 
