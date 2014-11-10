
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

   apt-get install git redis-server python python-redis python-lxml
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

  