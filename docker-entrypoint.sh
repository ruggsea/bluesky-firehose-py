#!/bin/bash
set -e

# Function to show usage
show_usage() {
    echo "Bluesky Firehose Archiver Docker Container"
    echo ""
    echo "Usage:"
    echo "  docker run [options] bluesky-archiver [command]"
    echo ""
    echo "Commands:"
    echo "  archive         Run the archiver (default)"
    echo "  archive-all     Archive all records"
    echo "  archive-non-posts  Archive non-post records only"
    echo "  bash           Open bash shell"
    echo "  help           Show this help"
    echo ""
    echo "Environment Variables:"
    echo "  BLUESKY_USERNAME    Bluesky username"
    echo "  BLUESKY_PASSWORD    Bluesky password"
    echo "  DEBUG              Enable debug mode (true/false)"
    echo "  STREAM             Stream posts to stdout (true/false)"
    echo "  MEASURE_RATE       Track posts per minute (true/false)"
    echo "  GET_HANDLES        Resolve handles (true/false)"
    echo "  CURSOR             Unix microseconds timestamp to start from"
    echo ""
    echo "Examples:"
    echo "  # Basic archiving"
    echo "  docker run -v \$(pwd)/data:/app/data bluesky-archiver"
    echo ""
    echo "  # Archive with credentials"
    echo "  docker run -e BLUESKY_USERNAME=user.bsky.social \\"
    echo "             -e BLUESKY_PASSWORD=password \\"
    echo "             -v \$(pwd)/data:/app/data bluesky-archiver"
    echo ""
    echo "  # Archive all records with debug"
    echo "  docker run -e DEBUG=true \\"
    echo "             -v \$(pwd)/data_everything:/app/data_everything \\"
    echo "             bluesky-archiver archive-all"
}

# Function to build python command arguments
build_args() {
    local args=()
    
    # Add credentials if provided
    if [ -n "$BLUESKY_USERNAME" ]; then
        args+=("--username" "$BLUESKY_USERNAME")
    fi
    
    if [ -n "$BLUESKY_PASSWORD" ]; then
        args+=("--password" "$BLUESKY_PASSWORD")
    fi
    
    # Add flags if environment variables are set to true
    if [ "$DEBUG" = "true" ]; then
        args+=("--debug")
    fi
    
    if [ "$STREAM" = "true" ]; then
        args+=("--stream")
    fi
    
    if [ "$MEASURE_RATE" = "true" ]; then
        args+=("--measure-rate")
    fi
    
    if [ "$GET_HANDLES" = "true" ]; then
        args+=("--get-handles")
    fi
    
    if [ -n "$CURSOR" ]; then
        args+=("--cursor" "$CURSOR")
    fi
    
    echo "${args[@]}"
}

# Main command handling
case "${1:-archive}" in
    archive)
        echo "Starting Bluesky Archiver (posts only)..."
        args=($(build_args))
        exec python src/main.py "${args[@]}"
        ;;
    archive-all)
        echo "Starting Bluesky Archiver (all records)..."
        args=($(build_args))
        exec python src/main.py "${args[@]}" --archive-all
        ;;
    archive-non-posts)
        echo "Starting Bluesky Archiver (non-posts only)..."
        args=($(build_args))
        exec python src/main.py "${args[@]}" --archive-non-posts
        ;;
    bash)
        exec /bin/bash
        ;;
    help|--help|-h)
        show_usage
        exit 0
        ;;
    python)
        # Allow direct python execution
        shift
        exec python "$@"
        ;;
    *)
        # If it looks like Python arguments, run with python
        if [[ "$1" == src/* ]] || [[ "$1" == *.py ]]; then
            exec python "$@"
        else
            echo "Unknown command: $1"
            echo "Use 'help' to see available commands"
            exit 1
        fi
        ;;
esac
