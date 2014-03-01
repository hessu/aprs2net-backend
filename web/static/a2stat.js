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
	return D.getUTCFullYear() + '-' + lz(D.getUTCMonth()+1) + '-' + lz(D.getUTCDate())
		+ ' ' + lz(D.getUTCHours()) + ':' + lz(D.getUTCMinutes()) + ':' + lz(D.getUTCSeconds())
		+ 'z';
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
	
	$scope.rowClick = function(pe) {
		//$scope.liveModel = 'Selected';
		//plotEvent(pe);
	}
	
	$scope.servers = servers;
	
	var ajax_update = function($scope, $http) {
		var config = {
			'params': {}
		};
		
		if (evq['seq'] > 0) {
			config['params']['seq'] = evq['seq'];
		}
		
		$http.get('/api/upd', config).success(function(d) {
			console.log('HTTP update received, status: ' + d['result']);
			
			$scope.evq = evq = d['evq'];
			
			if (d['ev']) {
				for (var i in d['ev']) {
					if (d['ev'][i].event == 'sqlend') {
						for (var ei = 0; ei < ev.length; ei++)
							if (ev[ei].id == d['ev'][i].id) {
								ev[ei].duration = d['ev'][i].duration;
								break;
							}
					} else
						ev.unshift(d['ev'][i]);
				}
				
				if ($scope.liveModel == 'Live')
					plotEvent(ev[0]);
			}
			
			setTimeout(function() { ajax_update($scope, $http); }, 1200);
		}).error(function(data, status, headers, config) {
			console.log('HTTP update failed, status: ' + status);
			setTimeout(function() { ajax_update($scope, $http); }, 1200);
		});
	};
	
	var full_load = function($scope, $http) {
		var config = {
			'params': {}
		};
		
		$http.get('/api/full', config).success(function(d) {
			console.log('HTTP full download received, status: ' + d['result']);
			
			$scope.evq = evq = d['evq'];
			var a = [];
			var s = d['servers'];
			$scope.servers = s;
			
			//setTimeout(function() { ajax_update($scope, $http); }, 1200);
		}).error(function(data, status, headers, config) {
			console.log('HTTP full download failed, status: ' + status);
			//setTimeout(function() { ajax_update($scope, $http); }, 1200);
		});
	};
	
	full_load($scope, $http);
}]);


//-->
