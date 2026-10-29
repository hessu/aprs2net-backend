
aprs2.net backend code, model 2
===================================

Here's a little bit of code to poll aprs2.net servers and verify their availability.


License
----------

This code is released under the BSD license, which can be found in the file
LICENSE.


Installation
---------------

Install dependencies:

    apt-get install git redis-server python3 python3-redis python3-lxml python3-dnspython
    apt-get install supervisor nodejs nginx

Prepare:

    sudo adduser --system --disabled-login --group t2poll
    cd /opt

Download:

    sudo git clone -b release https://github.com/hessu/aprs2net-backend.git
   
Adjust nginx config:

    rm /etc/nginx/sites-enabled/default
    cd /etc/nginx/sites-enabled
    ln -s /opt/aprs2net-backend/conf-poller/nginx-vhost.conf t2poll.conf
    # edit /opt/aprs2net-backend/conf-poller/nginx-vhost.conf to fix the server hostname
    service nginx restart

Adjust poller config:

    cd /opt/aprs2net-backend
    sudo mkdir logs
    sudo chown t2poll:t2poll logs
    cd /opt/aprs2net-backend/poller
    cp poller-example.conf poller.conf
    # edit poller conf

Set up supervisord:

    cd /etc/supervisor/conf.d
    sudo ln -s /opt/aprs2net-backend/conf-poller/poller-supervisor.conf
    supervisorctl reload
  
Upgrading:

    cd /opt/aprs2net-backend
    sudo git fetch
    sudo git rebase
    
    # restart nginx, if changes were made to its config
    service nginx restart
    
    # restart poller and status web app
    service supervisor restart

Other operations:

    # to restart just the poller
    supervisorctl restart a2poller
    
    # to restart just the status web app
    supervisorctl restart a2web

Logs:

/opt/aprs2net-backend/logs contains log files for the web app and the
poller. /var/log/nginx contains logs of the nginx web server.
supervisord, which keeps the poller and web app processes running, logs in
/var/log/supervisor.



Tests
--------

The poller and the DNS driver have a test suite under poller/tests, run
with pytest. It covers the status page parsers for aprsc and javAPRSSrvr,
the scoring, the APRS-IS and IS2 port probes, the uplink and IS2 checks,
the portal configuration processing, the poller's result handling, the
primary/backup failover state machine, the BIND and cloudflare record push
paths, and the DNS driver's polling round. No network, redis or nameserver
is needed.

    apt-get install python3-pytest
    make test

The packages needed by the tests are listed in poller/requirements-test.txt
and can also be installed with pip through "make deps-test". The suite runs
on GitHub Actions for every push and pull request.
