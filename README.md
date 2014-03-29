
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

Download:

   cd /opt
   adduser t2poll, addgroup t2poll (TODO, work out details)
   git clone -b release https://github.com/hessu/aprs2net-backend.git

Adjust nginx config:

  rm /etc/nginx/sites-enabled/default
  cd /etc/nginx/sites-enabled && ln -s ...

