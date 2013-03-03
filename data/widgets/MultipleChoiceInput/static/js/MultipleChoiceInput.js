var mc = angular.module('mc', []);

// Sets the AngularJS interpolators as <[ and ]>, to not conflict with Django.
mc.config(function($interpolateProvider) {
  $interpolateProvider.startSymbol('<[');
  $interpolateProvider.endSymbol(']>');
});

function MultipleChoiceInput($scope) {
  $scope.submitAnswer = function(answer) {
    if (!answer) {
      return;
    }
    if (parent.location.pathname.indexOf('/learn') === 0) {
      window.parent.postMessage(
          {'submit': answer}, window.location.origin
      );
    }
  };
}
