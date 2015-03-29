#!/usr/bin/env python
# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import argparse
import ConfigParser
import logging
import os.path
import signal
import sys

try:
    from flask import Flask, g
except ImportError:
    print "Error: you need to install flask (`pip install flask`)"
    sys.exit(1)

from distributed.db import db
from distributed.scheduler import SchedulerThread
from views.api import blueprint as ApiBlueprint

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from lib.cuckoo.core.startup import drop_privileges

log = logging.getLogger(__name__)

def create_app(database_connection):
    app = Flask("Distributed Cuckoo")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_connection
    app.config["SECRET_KEY"] = os.urandom(32)

    app.register_blueprint(ApiBlueprint, url_prefix="/api")
    app.register_blueprint(ApiBlueprint, url_prefix="/api/v1")

    db.init_app(app)
    db.create_all(app=app)

    return app

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("host", nargs="?", default="127.0.0.1", help="Host to listen on.")
    p.add_argument("port", nargs="?", type=int, default=9003, help="Port to listen on.")
    p.add_argument("-u", "--user", type=str, help="Drop user privileges to this user.")
    p.add_argument("-s", "--settings", type=str, help="Settings file.")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging.")
    args = p.parse_args()

    if args.user:
        drop_privileges(args.user)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    log = logging.getLogger("cuckoo.distributed")

    if not args.settings:
        dirpath = os.path.abspath(os.path.dirname(__file__))
        conf_path = os.path.join(dirpath, "..", "conf", "distributed.conf")
        args.settings = conf_path

    s = ConfigParser.ConfigParser()
    s.read(args.settings)

    if not s.get("distributed", "database"):
        sys.exit("Please configure a database connection.")

    app = create_app(database_connection=s.get("distributed", "database"))

    # Note that we don't pop this app_context as that would result in losing
    # our variables in g.
    app_context = app.app_context()
    app_context.push()

    g.report_formats = []
    for report_format in s.get("distributed", "report_formats").split(","):
        g.report_formats.append(report_format.strip())

    if not g.report_formats:
        sys.exit("Please configure one or more reporting formats.")

    g.samples_directory = s.get("distributed", "samples_directory")

    if not g.samples_directory:
        sys.exit("Please configure a samples directory path.")

    if not os.path.isdir(g.samples_directory):
        os.makedirs(g.samples_directory)

    g.reports_directory = s.get("distributed", "reports_directory")

    if not g.reports_directory:
        sys.exit("Please configure a reports directory path.")

    if not os.path.isdir(g.reports_directory):
        os.makedirs(g.reports_directory)

    g.running = True
    g.statuses = {}
    g.verbose = args.verbose
    g.worker_processes = s.getint("distributed", "worker_processes")
    g.uptime_logfile = s.get("distributed", "uptime_logfile")
    g.interval = s.getint("distributed", "interval")
    g.batch_size = s.getint("distributed", "batch_size")

    t = SchedulerThread(app_context)
    t.daemon = True
    t.start()

    app.run(host=args.host, port=args.port)

    # If we reach here then the webserver has been killed - propagate this
    # to our scheduler, but wait for it to finish.
    log.info("Exited the webserver, waiting for the scheduler to finish.")
    g.running = False

    # Please, kill it more often ;-)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    t.join()
