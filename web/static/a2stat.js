<!--

function lz(i)
{
	if (i < 10)
		return '0' + i;
	
	return i;
}

function timestr(i)
{
	if (i === undefined)
		 return '';
	
	var D = new Date(i*1000);
	var N = new Date();
	
	if (N.getTime() - D.getTime() > 86400000)
		return D.getUTCFullYear() + '-' + lz(D.getUTCMonth()+1) + '-' + lz(D.getUTCDate());
	else
		return lz(D.getUTCHours()) + ':' + lz(D.getUTCMinutes()) + ':' + lz(D.getUTCSeconds()) + 'z';
}

function dur_str(i)
{
	if (i === undefined)
		 return '';
	
	var t;
	var s = '';
	var c = 0;
	
	if (i > 86400) {
		t = Math.floor(i/86400);
		i -= t*86400;
		s += t + 'd';
		c++;
	}
	if (i > 3600) {
		t = Math.floor(i / 3600);
		i -= t*3600;
		s += t + 'h';
		c++;
	}
	if (c > 1)
		return s;
		
	if (i > 60) {
		t = Math.floor(i / 60);
		i -= t*60;
		s += t + 'm';
		c++;
	}
	
	if (c)
		return s;
	
	return i.toFixed(0) + 's';
}

function dur_str_ms(i)
{
	if (i === undefined || i == -1)
		 return '';
	
	var t;
	var s = '';
	var c = 0;
	
	if (i > 3600000) {
		t = Math.floor(i / 3600000);
		i -= t*3600000;
		s += t + 'h';
		c++;
	}
	
	if (i > 60000) {
		t = Math.floor(i / 60000);
		i -= t*60000;
		s += t + 'm';
		c++;
	}
	
	if (c > 1)
		return s;
	
	if (i >= 10000) {
		t = Math.floor(i / 1000);
		i -= t*1000;
		s += t + 's';
		c++;
	}
	
	if (c)
		return s;
	
	i = i / 1000;
	
	return i.toFixed(3) + 's';
}

var evq = {};
var servermap = {};
var groupidmap = {};

/*
 *	give a go at using AngularJS
 */

var app = angular.module('aprs2status', []).
	config(function() {
		console.log('aprs2status module config');
	}).
	run(function() {
		console.log('aprs2status module run');
	});

/*
app.directive('buttonsRadio', function() {
	console.log("buttonsRadio setup");
	return {
		restrict: 'E',
		scope: { model: '=', options:'='},
		controller: function($scope) {
			console.log("buttonsRadio controller setup");
			$scope.activate = function(option){
				$scope.model = option;
			};      
		},
		template: "<button type='button' class='btn' "+
			"ng-class='{active: option == model}'"+
			"ng-repeat='option in options' "+
			"ng-click='activate(option)'>{{option}} "+
			"</button>"
	};
});
*/

app.filter('duration', function() { return dur_str; });
app.filter('duration_ms', function() { return dur_str_ms; });
app.filter('datetime', function() { return timestr; });

app.controller('a2stat', [ '$scope', '$http', function($scope, $http) {
	console.log('a2stat init');
	
	/*
	$scope.liveOptions = ["Live", "Selected"];
	$scope.liveModel = "Live";
	
	$scope.$watch('liveModel', function(v){
		console.log('changed', v);
	});
	*/
	
	/* Support for service status */
	$scope.nets = [
		{ id: 'rotate.aprs.net', name: 'Core' },
		{ id: 'hubs.aprs2.net', name: 'T2 Hubs' },
		{ id: 'rotate.aprs2.net', name: 'T2 Leafs' },
		{ id: 'cwop.aprs.net', name: 'CWOP' },
		{ id: 'firenet.aprs2.net', name: 'Firenet' }
	];
	
	var summary_update = function() {
		for (var i in $scope.nets) {
			var n = $scope.nets[i];
			console.log("summary_update group " + i + ": " + n.id);
			
			var clients = 0;
			var servers_ok = 0;
			var rate_out = 0;
			for (var d in groups[i]) {
				s = groups[i][d];
				if (!s.config) {
					console.log("No config in JSON: " + JSON.stringify(s));
					continue;
				}
				//console.log("   server " + s.config.id);
				if (s.status) {
					if (s.status.status == 'ok')
						servers_ok += 1;
						
					if (s.status.props) {
						if (s.status.props.clients)
							clients += s.status.props.clients;
						if (s.status.props.rate_bytes_out)
							rate_out += s.status.props.rate_bytes_out;
					}
				}
			}
			n.clients = clients;
			n.servers_ok = servers_ok;
			n.servers_count = groups[i].length;
			n.rate_out = rate_out;
			console.log("   " + servers_ok + " servers ok, " + clients + " clients");
		}
	};
	
	/* Poll log display support */
	$scope.showLog = false;
	
	var fetchLog = function(id) {
		var config = {
			'params': {
				'id': id
			}
		};
		
		$http.get('/api/slog', config).success(function(d) {
			console.log('Log fetched for id ' + id +', status: ' + d['result']);
			$scope.shownLog = d;
		});
	};
	
	$scope.rowClick = function(s) {
		$scope.shownServer = s;
		fetchLog(s.config.id);
	}
	
	var initial_load = 1;
	var setup_columns = function(cfg) {
		var cols = [
			[ 'config.id', 'Server ID' ],
			[ 'status.props.vers', 'Version' ],
			[ 'status.props.os', 'OS' ],
			[ 'status.last_test', 'Tested' ],
			[ 'status.last_change', 'Changed' ],
			[ 'status.props.clients', 'Clients' ],
			[ 'status.props.worst_load', 'C load' ],
			[ 'status.props.rate_bytes_out', 'B out/s' ]
		];
		
		if (cfg['master']) {
			cols.push([ 'status.c', 'OK' ]);
		}
			
		cols.push([ 'status.props.score', 'Score' ],
			[ 'status.avail_30', 'Avail' ],
			[ 'status.props.info', 'Info' ]
		);
		$scope.columns = cols;
		
		$scope.sort = {
			column: 'config.id',
			descending: false
		};
	}
	
	$scope.sortIndicator = function(column) {
		if (column == $scope.sort.column) {
			return 'glyphicon glyphicon-sort-by-attributes'
				+ (($scope.sort.descending) ? '-alt' : '');
		}
		return '';
	};
	
	$scope.changeSorting = function(column) {
		var sort = $scope.sort;
		
		if (sort.column == column) {
			sort.descending = !sort.descending;
		} else {
			sort.column = column;
			sort.descending = false;
		}
	};
	
	/* Ajax updates */
	
	var full_load;
	var ajax_update = function($scope, $http) {
		var config = {
			'timeout': 35000,
			'params': { 'seq': evq['seq'] }
		};
		
		$http.get('/api/upd', config).success(function(d) {
			console.log('HTTP update received, status: ' + d['result']);
			
			if (d['result'] == 'full') {
				console.log('got full reload...');
				process_full_load($scope, $http, d);
				return;
			}
			if (d['result'] != 'upd') {
				console.log('result ' + d['result']);
				return;
			}
			
			$scope.evq = evq = d['evq'];
			
			if (d['ev']) {
				for (var i in d['ev']) {
					var srvr = d['ev'][i];
					var id = srvr['config']['id'];
					//console.log("  server " + id);
					var idx = servermap[id];
					if (idx) {
						groups[groupmap[id]][groupidmap[id]] = srvr;
						//console.log("   added: " + JSON.stringify(srvr));
						if ($scope.shownServer && id == $scope.shownServer.config.id) {
							$scope.shownServer = srvr;
							//console.log("  shown server, fetching log");
							fetchLog(id);
						}
					} else {
						// TODO: add new server
					}
				}
			}
			
			summary_update();
			
			setTimeout(function() { ajax_update($scope, $http); }, 1200);
		}).error(function(data, status, headers, config) {
			console.log('HTTP update failed, status: ' + status);
			setTimeout(function() { ajax_update($scope, $http); }, 5000);
		});
	};
	
	process_full_load = function($scope, $http, d) {
		$scope.evq = evq = d['evq'];
		$scope.cfg = d['cfg'];
		var a = [];
		var servers = d['servers'];
		var rotatestat = d['rotatestat'];
		servermap = {};
		groupmap = {};
		
		groups = [];
		for (var i in $scope.nets) {
			var n = $scope.nets[i];
			//console.log("  group " + i + ": " + n.id);
			var rot = d['rotates'][n.id];
			
			for (var id in rot['members']) {
				groupmap[rot['members'][id]] = groups.length;
				//console.log("     - " + rot['members'][id]);
			}
			groups.push([]);
		}
		
		tables = {};
		for (var i in servers) {
			var id = servers[i]['config']['id'];
			//console.log('id ' + id + ' i ' + i);
			if (rotatestat && rotatestat[id] && rotatestat[id]['rotate.aprs2.net']) {
				servers[i]['rotate'] = 1;
				//console.log(' --- is in rotate');
			}
			servermap[id] = i;
			groupidmap[id] = groups[groupmap[id]].length
			groups[groupmap[id]].push(servers[i]);
			//console.log("   " + id + " pushed to " + groupmap[id] + " at position " + groupidmap[id] + " " + JSON.stringify(servers[i]));
		}
		
		$scope.groups = groups;
		$scope.rotates = d['rotates'];
		
		summary_update();
		
		setTimeout(function() { ajax_update($scope, $http); }, 1200);
	}
	
	full_load = function($scope, $http) {
		var config = { 'params': { } };
		$http.get('/api/full', config).success(function(d) {
			console.log('HTTP full download received, status: ' + d['result']);
			
			if (initial_load) {
				initial_load = 0;
				setup_columns(d['cfg']);
			}
			
			process_full_load($scope, $http, d);
		}).error(function(data, status, headers, config) {
			console.log('HTTP full download failed, status: ' + status);
			setTimeout(function() { full_load($scope, $http); }, 10000);
		});
	};
	
	full_load($scope, $http);
}]);


//-->
