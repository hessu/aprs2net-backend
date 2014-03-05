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
var servers = [];
var servermap = {};

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
		{ id: 'core', name: 'Core' },
		{ id: 't2hub', name: 'T2 Hubs' },
		{ id: 't2leaf', name: 'T2 Leafs' },
		{ id: 'cwop', name: 'CWOP' },
		{ id: 'fire', name: 'Firenet' }
	];
	
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
	
	$scope.columns = [
		[ 'config.id', 'Server ID' ],
		[ 'status.props.vers', 'Version' ],
		[ 'status.props.os', 'OS' ],
		[ 'status.last_test', 'Tested' ],
		[ 'status.last_ok', 'Last OK' ],
		[ 'status.props.clients', 'Clients' ],
		[ 'status.props.worst_load', 'C load' ],
		[ 'status.props.score', 'Score' ],
		[ 'status.props.info', 'Info' ]
	];
	
	$scope.sort = {
		column: 'config.id',
		descending: false
	};
	
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
	
	$scope.servers = servers;
	
	var full_load;
	var ajax_update = function($scope, $http) {
		var config = {
			'timeout': 30000,
			'params': { 'seq': evq['seq'] }
		};
		
		$http.get('/api/upd', config).success(function(d) {
			console.log('HTTP update received, status: ' + d['result']);
			
			if (d['result'] == 'reload') {
				console.log('going for full reload...');
				full_load($scope, $http);
				return;
			}
			if (d['result'] != 'ok') {
				console.log('result ' + d['result']);
				return;
			}
			
			$scope.evq = evq = d['evq'];
			
			if (d['ev']) {
				for (var i in d['ev']) {
					var srvr = d['ev'][i];
					var id = srvr['config']['id'];
					console.log("  server " + id);
					var idx = servermap[id];
					if (idx) {
						servers[idx] = srvr;
						if ($scope.shownServer && id == $scope.shownServer.config.id) {
							$scope.shownServer = srvr;
							console.log("  shown server, fetching log");
							fetchLog(id);
						}
					} else {
						// TODO: add new server
					}
				}
			}
			
			setTimeout(function() { ajax_update($scope, $http); }, 1200);
		}).error(function(data, status, headers, config) {
			console.log('HTTP update failed, status: ' + status);
			setTimeout(function() { ajax_update($scope, $http); }, 5000);
		});
	};
	
	full_load = function($scope, $http) {
		var config = { 'params': { } };
		$http.get('/api/full', config).success(function(d) {
			console.log('HTTP full download received, status: ' + d['result']);
			
			$scope.evq = evq = d['evq'];
			var a = [];
			var s = d['servers'];
			servermap = {};
			$scope.servers = servers = s;
			
			for (var i in s) {
				servermap[s[i]['config']['id']] = i;
			}
			
			setTimeout(function() { ajax_update($scope, $http); }, 1200);
		}).error(function(data, status, headers, config) {
			console.log('HTTP full download failed, status: ' + status);
			setTimeout(function() { full_load($scope, $http); }, 10000);
		});
	};
	
	full_load($scope, $http);
}]);


//-->
