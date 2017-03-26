from api import app


if __name__ == "__main__":
    from geventwebsocket import WebSocketServer

    server = WebSocketServer(('', 5000), app)
    print '  Serving at host 0.0.0.0:5000...\n'
    server.serve_forever()
