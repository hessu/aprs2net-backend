
var http = require('http'),
	express = require("express"),
	util = require("util"),
	events = require("events"),
	redis = require("node_redis");
                
util.log("startup");

var listen_addr = 'localhost';
var redis_dbid = 0;

var ops = {};


parse_cmdline();

/* Redis keys used */
kServer = 'aprs2.server';
kServerStatus = 'aprs2.serverstat';
kServerLog = 'aprs2.serverlog';
kPollQueue = 'aprs2.pollq';
kScore = 'aprs2.score';
kChannelStatus = 'aprs2.chStatus';
kChannelStatusDns = 'aprs2.chStatusDns';
kWebConfig = 'aprs2.webconfig';
kRotate = 'aprs2.rotate';

if (ops['dns_driver']) {
	redis_dbid = 1;
	redis_channel = kChannelStatusDns;
} else {
	redis_channel = kChannelStatus;
}

var evq_keep_events = 30;

var evq_seq = 10;
var evq_len = 0;

var evq = [];

var emitter = new events.EventEmitter;

function parse_cmdline() {
	var stdio = require('stdio');
	ops = stdio.getopt({
		'listen_any': {key: 'a', description: 'Listen on INADDR_ANY instead of loopback'},
		'port': {key: 'p', args: 1, description: 'Specify TCP port to listen on'},
		'dns_driver': {key: 'd', description: 'Run in DNS driver mode'}
	});
	
	if (ops['listen_any'])
		listen_addr = '0.0.0.0';
	if (!ops['port'])
		ops['port'] = '8036';
}

function append_event(j) {
	var m = JSON.parse(j);
	
	if (m['reload']) {
		util.log("got new event for full reload");
		emitter.emit("event:notify", 0);
		return;
	}
	
	evq.push(m);
	evq_len++;
	evq_seq++;
	util.log("got new event " + evq_seq + " evq len " + evq_len);
	
	while (evq_len > evq_keep_events) {
		evq.shift();
		evq_len--;
		//util.log("expired from evq, len now " + evq.length + " / " + evq_len)
	}
	
	//util.log("event listener count: " + util.inspect(emitter.listeners('event:notify')));
	emitter.emit("event:notify", evq_seq);
};

function last_events(n)
{
	var a = [];
	var l = evq.length;
	for (var i = l - n; i < l; i++)
		a.push(evq[i]);
	
	util.log("last_events returning " + a.length + " events");
	
	return a;
}

function upd_response(seq, res)
{
	var seq_dif = evq_seq - seq;
	if (seq_dif > evq.length)
		seq_dif = evq.length;
	util.log("client is " + seq_dif + " events late");
	
	var ev;
	if (seq_dif > 0)
		ev = last_events(seq_dif);
	else
		ev = [];
	
	res.setHeader('Cache-Control', 'no-cache');
	res.json({
		'result': 'upd',
		'evq': {
			'seq': evq_seq,
			'len': evq_len
		},
		'ev': ev
	});
}

function handle_full_status(req, res)
{
	util.log("full req: " + JSON.stringify(req.query));
	
	generate_full_status(req, res);
}

function generate_full_status(req, res)
{
	red.hgetall(kServerStatus, function (err, stats) {
		for (i in stats)
			stats[i] = JSON.parse(stats[i]);
		
		red.hgetall(kRotate, function (err, rots) {
			for (i in rots)
				rots[i] = JSON.parse(rots[i]);
			
			red.hgetall(kServer, function (err, confs) {
				var a = [];
				
				for (i in confs) {
				        var conf = JSON.parse(confs[i]);
				        // do not display servers marked as deleted
				        if (conf['deleted'])
				        	continue;
				        
				        // trim unnecessary elements from JSON
				        delete conf['deleted'];
				        delete conf['host'];
				        delete conf['domain'];
				        if (!conf['out_of_service'])
				        	delete conf['out_of_service'];
				        
					if (stats[i]) {
						a.push({
							'config': conf,
							'status': stats[i]
						});
					}
				}
				
				red.get(kWebConfig, function(err, cfg) {
					cfg = JSON.parse(cfg);
					
					res.setHeader('Cache-Control', 'no-cache');
					res.json({
						'result': 'full',
						'cfg': cfg,
						'evq': {
							'seq': evq_seq,
							'len': evq_len
						},
						'rotates': rots,
						'servers': a
					});
				});
			});
		});
	});
	
}

var handle_upd = function(req, res) {
	util.log("upd req: " + JSON.stringify(req.query));
	
	var seq = parseInt(req.query['seq']);
	if (seq == undefined) {
		util.log("no sequence number given");
		res.setHeader('Cache-Control', 'no-cache');
		res.json({ 'result': 'fail' });
		return;
	}
	if (seq > evq_seq) {
		//console.log("client seq " + seq + " > my seq " + evq_seq + " - starting from -1");
		seq = -1;
	} else if (evq_seq - seq > evq_len) {
		//console.log("client is too late, returning full reload");
		generate_full_status(req, res);
		return;
	}
	
	//util.log("updating with seq " + seq);
	var seq_dif = evq_seq - seq;
	
	if (seq_dif == 0) {
		//util.log("going longpoll");
		
		/* Set a timeout, so that we can close the longpoll request
		 * from the server side - client-side timeout will close
		 * the HTTP connection and require a new TCP setup.
		 * Would loose the benefits of keepalive.
		 */
		var tout = setTimeout(function() {
			util.log("sending longpoll timeout");
			emitter.removeListener("event:notify", notify);
			upd_response(seq, res);
		}, 25000);
		
		/* handler for sending response */
		var notify = function(id) {
			clearTimeout(tout);
			util.log("sending longpoll response, seq now " + id);
			if (id == 0) {
				util.log("  ... full response");
				generate_full_status(req, res);
			} else {
				util.log("  ... upd response");
				upd_response(seq, res);
			}
		};
		
		// when we have an event, return response
		emitter.once("event:notify", notify);
		
		// if the client closes, remove listener
		req.on("close", function() {
			clearTimeout(tout);
			util.log("client closed in middle of longpoll");
			emitter.removeListener("event:notify", notify);
		});
		return;
	}
	
	upd_response(seq, res);
};

var handle_slog = function(req, res) {
	util.log("slog req: " + JSON.stringify(req.query));
	
	var id = req.query['id'];
	if (id == undefined) {
		util.log("no server ID given");
		res.setHeader('Cache-Control', 'no-cache');
		res.json({ 'result': 'fail' });
		return;
	}
	
	red.hget(kServerLog, id, function (err, log) {
		log = JSON.parse(log);
		res.setHeader('Cache-Control', 'no-cache');
		res.json({ 'result': 'ok', 't': log['t'], 'log': log['log'] });
	});
};

/* Set up the redis client */
red = redis.createClient();
red.on("error", function (err) {
	util.log("Redis client error: " + err);
});

red.select(redis_dbid, function() {
	util.log("Selected database " + redis_dbid);
	init();
});


function init() {
	/* Subscribe to updates */
	red_sub = redis.createClient();
	red_sub.on("message", function(channel, message) {
		util.log("channel " + channel + ": " + message);
		append_event(message);
	});
	util.log("Listening for updates on " + redis_channel);
	red_sub.subscribe(redis_channel);

	/* Set up the express app */
	var app = express();
	app.configure(function() {
		app.use(express.bodyParser());
		app.use(express.methodOverride());
		app.use(app.router);
		app.use(express.static("static"));
		app.use(express.errorHandler({ dumpExceptions: true, showStack: true }));
	});

	app.get('/api/full', handle_full_status); /* fetch full server list */
	app.get('/api/upd', handle_upd); /* fetch updates to servers */
	app.get('/api/slog', handle_slog); /* fetch a poll log of a server */
	
	var listen_port = parseInt(ops['port']);

	util.log("aprs2-status web service set up, starting listener on port " + listen_port);

	app.listen(listen_port, listen_addr);
}

