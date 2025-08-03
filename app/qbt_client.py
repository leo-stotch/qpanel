import qbittorrentapi
from urllib.parse import urlparse

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