import websockets
import asyncio
import json
from datetime import datetime
import os
from atproto import Client
from typing import Dict, Optional, List, AsyncGenerator
from asyncio import Queue
import logging
import aiofiles
import sys

class BlueskyArchiver:
    def __init__(
        self, 
        username: Optional[str] = None, 
        password: Optional[str] = None, 
        debug: bool = False, 
        stream: bool = False, 
        measure_rate: bool = False,
        get_handles: bool = False,
        cursor: Optional[int] = None,  # Unix microseconds timestamp
        archive_all: bool = False,  # Flag to archive all records
        archive_non_posts: bool = False  # Flag to archive everything except posts
    ):
        """Initialize the Bluesky Archiver."""
        # Configure logging
        logging.getLogger('websockets').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)

        if archive_all and archive_non_posts:
            raise ValueError("Cannot use both archive_all and archive_non_posts modes simultaneously")

        self.debug = debug
        self.stream = stream
        self.measure_rate = measure_rate
        self.get_handles = get_handles
        self.cursor = cursor
        self.post_count = 0
        self.posts_saved = 0
        self.start_time = None
        self.running = True

        # Initialize client
        self.client = Client()
        if username and password:
            self.client.login(username, password)

        self.uri = "wss://jetstream2.us-east.bsky.network/subscribe"
        self.handle_cache: Dict[str, str] = {}
        self.resolving_dids: set = set()  # Track DIDs currently being resolved
        self.handle_semaphore = asyncio.Semaphore(10)  # Limit concurrent handle resolutions

        # Initialize queues
        # Bounded queues: when disk writes fall behind, the websocket reader blocks
        # instead of buffering without limit (which previously led to OOM)
        self.raw_queue: Queue = Queue(maxsize=5000)  # Posts from websocket with None handles
        self.processed_queue: Queue = Queue(maxsize=5000)  # Posts with resolved handles

        # Initialize background tasks
        self.disk_task: asyncio.Task = None
        self.handle_task: asyncio.Task = None
        self.websocket_task: asyncio.Task = None

        # Set up logging
        logging.basicConfig(
            level=logging.DEBUG if self.debug else logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

        self.archive_all = archive_all
        self.archive_non_posts = archive_non_posts

    async def get_handle(self, did: str) -> Optional[str]:
        """Retrieve handle for a given DID."""
        if did in self.handle_cache:
            return self.handle_cache[did]
        # If this DID is already being resolved, wait a bit and check cache again
        if did in self.resolving_dids:
            await asyncio.sleep(0.1)
            return self.handle_cache.get(did)

        self.resolving_dids.add(did)
        try:
            async with self.handle_semaphore:
                response = await asyncio.to_thread(self.client.com.atproto.repo.describe_repo, {'repo': did})
                handle = response.handle
                self.handle_cache[did] = handle
                return handle
        except Exception as e:
            logging.error(f"🔴 Error getting handle for {did}: {e}")
            return None
        finally:
            self.resolving_dids.remove(did)

    async def save_posts_async(self, posts: List[dict]):
        if self.measure_rate and self.start_time is None:
            self.start_time = datetime.now()

        """Asynchronously save posts to JSONL files, organized by hour."""
        if self.archive_all or self.archive_non_posts:
            # Save raw records in data_everything directory
            # Determine the output directory based on mode
            if self.archive_all:
                base_dir = "data_everything"
            else:  # archive_non_posts
                base_dir = "data_non_posts"

            # Group by file so each file is opened once per batch (not once per record)
            records_by_hour = {}
            for record in posts:
                post_time = datetime.fromtimestamp(record["time_us"] / 1_000_000)
                date_dir = post_time.strftime('%Y-%m/%d')
                hour_filename = post_time.strftime('records_%Y%m%d_%H.jsonl')
                full_path = f"{base_dir}/{date_dir}/{hour_filename}"
                records_by_hour.setdefault(full_path, []).append(record)

            for full_path, hour_records in records_by_hour.items():
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                async with aiofiles.open(full_path, 'a', encoding='utf-8') as f:
                    await f.write(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in hour_records))
            
            self.posts_saved += len(posts)
            
            if self.measure_rate:
                self.post_count += len(posts)
                now = datetime.now()
                if (now - self.last_rate_check).total_seconds() >= 10 if hasattr(self, 'last_rate_check') else True:
                    elapsed_minutes = (now - self.start_time).total_seconds() / 60
                    rate = self.post_count / elapsed_minutes
                    estimated_daily = rate * 60 * 24
                    logging.info(f"Current rate: {rate:.1f} records/minute (est. {int(estimated_daily):,} records/day)")
                    self.last_rate_check = now
            return

        posts_by_hour = {}
        for post in posts:
            post_time = datetime.fromtimestamp(post["time_us"] / 1_000_000)
            date_dir = post_time.strftime('%Y-%m/%d')
            hour_filename = post_time.strftime('posts_%Y%m%d_%H.jsonl')
            full_path = f"data/{date_dir}/{hour_filename}"

            posts_by_hour.setdefault(full_path, []).append(post)

        for full_path, hour_posts in posts_by_hour.items():
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            async with aiofiles.open(full_path, 'a', encoding='utf-8') as f:
                await f.write('\n'.join(json.dumps(post, ensure_ascii=False) for post in hour_posts) + '\n')
            self.posts_saved += len(hour_posts)

        if self.measure_rate:
            self.post_count += len(posts)
            now = datetime.now()
            if (now - self.last_rate_check).total_seconds() >= 10 if hasattr(self, 'last_rate_check') else True:
                elapsed_minutes = (now - self.start_time).total_seconds() / 60
                rate = self.post_count / elapsed_minutes
                estimated_daily = rate * 60 * 24
                logging.info(f"Current rate: {rate:.1f} posts/minute (est. {int(estimated_daily):,} posts/day)")
                self.last_rate_check = now

    async def get_handle_and_update(self, post: dict):
        """Get handle for a post and update post and queue when done."""
        handle = await self.get_handle(post['did'])
        post['handle'] = handle
        await self.processed_queue.put([post])

    async def handle_processor(self):
        """Process posts from raw_queue, resolve handles, and put in processed_queue."""
        while self.running:
            try:
                posts = await self.raw_queue.get()
                if self.get_handles:
                    # Process all posts in parallel
                    tasks = []
                    for post in posts:
                        if post['did'] not in self.handle_cache:
                            tasks.append(self.get_handle_and_update(post))
                        else:
                            # If handle is in cache, send directly to processed queue
                            post['handle'] = self.handle_cache[post['did']]
                            tasks.append(self.processed_queue.put([post]))
                    
                    # Wait for all handle resolutions and queue updates to complete
                    if tasks:
                        await asyncio.gather(*tasks)
                else:
                    # Skip handle resolution, just forward to processed queue
                    await self.processed_queue.put(posts)

                self.raw_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"🔴 Error in handle processor: {e}")

    async def disk_worker(self) -> None:
        """Background task to handle disk operations."""
        while self.running:
            try:
                posts = list(await self.processed_queue.get())
                taken = 1
                # Drain whatever is already queued so writes happen in batches
                while taken < 1000 and not self.processed_queue.empty():
                    posts.extend(self.processed_queue.get_nowait())
                    taken += 1
                await self.save_posts_async(posts)
                for _ in range(taken):
                    self.processed_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"🔴 Error in disk worker: {e}")

    async def archive_websocket_listener(self):
        """Continuously listen to the WebSocket and enqueue posts."""
        while self.running:
            try:
                params = {}  # No filters when archive_all is True
                if self.cursor:
                    if self.debug:
                        logging.debug(f"Starting playback from cursor: {self.cursor}")
                    params["cursor"] = str(self.cursor)

                url = f"{self.uri}"
                if params:
                    url += f"?{'&'.join(f'{k}={v}' for k, v in params.items())}"

                async with websockets.connect(url) as archive_websocket:
                    if self.debug:
                        logging.debug("🟢 Connected to firehose for archiving")
                    async for message in archive_websocket:
                        if not self.running:
                            break
                        data = json.loads(message)
                        
                        if data.get("kind") != "commit":
                            continue
                            
                        commit = data.get("commit", {})
                        if commit.get("operation") != "create":
                            continue

                        # Check if it's a post
                        is_post = commit.get("collection") == "app.bsky.feed.post"
                        
                        # Handle different archiving modes
                        if self.archive_all:
                            await self.raw_queue.put([data])
                        elif self.archive_non_posts:
                            if not is_post:
                                await self.raw_queue.put([data])
                        else:  # Posts only mode
                            if is_post:
                                did = data.get('did')
                                post_record = {
                                    'handle': None,
                                    'record': commit.get('record'),
                                    "rkey": commit.get('rkey'),
                                    'did': did,
                                    'time_us': data.get('time_us')
                                }
                                await self.raw_queue.put([post_record])

                        self.cursor = data.get('time_us')

                        if self.stream and is_post and 'text' in commit.get('record', {}):
                            sys.stdout.write(f"🖊️: {commit['record']['text']}\n")
                            sys.stdout.flush()

            except asyncio.CancelledError:
                break  # Exit cleanly on cancellation
            except websockets.exceptions.ConnectionClosed as e:
                if self.running:  # Only try to reconnect if we're still running
                    logging.warning(f"🔴 Connection closed: {e}. Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                if self.running:
                    logging.error(f"🔴 Unexpected error: {e}. Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def archive_posts(self):
        """Start archiving posts with continuous WebSocket listening."""
        # Start background tasks
        self.websocket_task = asyncio.create_task(self.archive_websocket_listener())
        self.handle_task = asyncio.create_task(self.handle_processor())
        self.disk_task = asyncio.create_task(self.disk_worker())

        # Wait for background tasks to complete
        await asyncio.gather(
            self.websocket_task,
            self.handle_task,
            self.disk_task
        )

    async def cleanup(self):
        """Clean up background tasks."""
        self.running = False
        if not hasattr(self, '_cleanup_started'):
            self._cleanup_started = True
        else:
            return

        logging.info("Shutting down... Saving remaining posts...")
        
        # First, save any remaining posts in the queues
        remaining_posts = 0
        # Process any remaining raw posts
        while not self.raw_queue.empty():
            try:
                posts = self.raw_queue.get_nowait()
                # Process all posts in parallel
                tasks = []
                for post in posts:
                    if post['did'] not in self.handle_cache:
                        tasks.append(self.get_handle_and_update(post))
                    else:
                        # If handle is in cache, send directly to processed queue
                        post['handle'] = self.handle_cache[post['did']]
                        tasks.append(self.processed_queue.put([post]))
                
                # Wait for all handle resolutions and queue updates to complete
                if tasks:
                    await asyncio.gather(*tasks)
                self.raw_queue.task_done()
            except asyncio.QueueEmpty:
                break

        # Save processed posts
        while not self.processed_queue.empty():
            try:
                posts = self.processed_queue.get_nowait()
                remaining_posts += len(posts)
                await self.save_posts_async(posts)
                self.processed_queue.task_done()
            except asyncio.QueueEmpty:
                break

        if remaining_posts > 0:
            logging.info(f"Saved {remaining_posts} remaining posts during shutdown")

        tasks = []
        for task in [self.websocket_task, self.disk_task, self.handle_task]:
            if task and not task.done():
                task.cancel()
                tasks.append(task)
        
        if tasks:
            try:
                # Wait for tasks to complete with a timeout
                await asyncio.wait(tasks, timeout=5.0)
            except asyncio.CancelledError:
                pass  # Ignore cancellation during cleanup
            except Exception as e:
                logging.error(f"Error during cleanup: {e}")

    def stop(self):
        """Stop the archiver gracefully."""
        self.running = False  # Ensure running is set to False first
        # Create cleanup task with a timeout
        async def cleanup_with_timeout():
            try:
                await asyncio.wait_for(self.cleanup(), timeout=5.0)
            except asyncio.TimeoutError:
                logging.warning("Cleanup timed out after 5 seconds")
                # Force exit after timeout
                import sys
                sys.exit(0)
            except Exception as e:
                logging.error(f"Error during cleanup: {e}")
                sys.exit(1)
        
        # Run cleanup and wait for it
        asyncio.create_task(cleanup_with_timeout())
        
        # Only show stats once during cleanup
        if self.measure_rate and self.start_time and not hasattr(self, '_cleanup_started'):
            elapsed_minutes = (datetime.now() - self.start_time).total_seconds() / 60
            if elapsed_minutes > 0:
                rate = self.post_count / elapsed_minutes
                estimated_daily = rate * 60 * 24
                logging.info(f"Final rate: {rate:.1f} posts/minute")
                logging.info(f"Estimated daily volume: {int(estimated_daily):,} posts/day")
                logging.info(f"Total posts collected: {self.post_count:,} (saved: {self.posts_saved:,})")
                if self.post_count != self.posts_saved:
                    logging.warning(f"⚠️ Discrepancy: {self.post_count - self.posts_saved:,} posts were collected but not saved")

