
var http = require('http'),
	express = require("express"),
	util = require("util"),
	events = require("events"),
	redis = require("node_redis");
                
util.log("startup");

/* Redis keys used */
kServer = 'aprs2.server';
kServerStatus = 'aprs2.serverstat';
kServerLog = 'aprs2.serverlog';
kPollQueue = 'aprs2.pollq';
kScore = 'aprs2.score';
kChannelStatus = 'aprs2.chStatus';
kWebConfig = 'aprs2.webconfig';

var evq_keep_events = 30;

var evq_seq = -1;
var evq_len = 0;

var evq = [];

/* Set up the redis client */
red = redis.createClient();
red.on("error", function (err) {
	util.log("Redis client error: " + err);
});

/* Subscribe to updates */
red_sub = redis.createClient();
red_sub.on("message", function(channel, message) {
	util.log("channel " + channel + ": " + message);
	append_event(message);
});
red_sub.subscribe(kChannelStatus);

/* Set up the express app */
var app = express();
app.configure(function() {
	app.use(express.bodyParser());
	app.use(express.methodOverride());
	app.use(app.router);
	app.use(express.static("static"));
	app.use(express.errorHandler({ dumpExceptions: true, showStack: true }));
});

var emitter = new events.EventEmitter;

function append_event(j) {
	var m = JSON.parse(j);
	evq.push(m);
	evq_len++;
	evq_seq++;
	//util.log("got new event " + evq_seq + " evq len " + evq_len);
	
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
		'result': 'ok',
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
	
	red.hgetall(kServerStatus, function (err, stats) {
		for (i in stats)
			stats[i] = JSON.parse(stats[i]);
		
		red.hgetall(kServer, function (err, confs) {
			var a = [];
			
			for (i in confs) {
				if (stats[i]) {
					a.push({
						'config': JSON.parse(confs[i]),
						'status': stats[i]
					});
				}
			}
			
			red.get(kWebConfig, function(err, cfg) {
				cfg = JSON.parse(cfg);
				
				res.setHeader('Cache-Control', 'no-cache');
				res.json({
					'result': 'ok',
					'cfg': cfg,
					'evq': {
						'seq': evq_seq,
						'len': evq_len
					},
					'servers': a
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
		//console.log("client is too late, doing full reload");
		res.setHeader('Cache-Control', 'no-cache');
		res.json({ 'result': 'reload' });
		return;
	}
	
	//util.log("updating with seq " + seq);
	var seq_dif = evq_seq - seq;
	
	if (seq_dif == 0) {
		//util.log("going longpoll");
		// handler function
		var notify = function(id) {
			util.log("sending longpoll response, seq now " + id);
			upd_response(seq, res);
		};
		// when we have an event, return response
		emitter.once("event:notify", notify);
		// if the client closes, remove listener
		req.on("close", function() { util.log("client closed in middle of longpoll"); emitter.removeListener("event:notify", notify); });
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

app.get('/api/full', handle_full_status); /* fetch full server list */
app.get('/api/upd', handle_upd); /* fetch updates to servers */
app.get('/api/slog', handle_slog); /* fetch a poll log of a server */

util.log("aprs2-status web service set up, starting listener");

app.listen(8036);


