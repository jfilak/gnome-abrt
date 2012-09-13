## Copyright (C) 2012 ABRT team <abrt-devel-list@redhat.com>
## Copyright (C) 2001-2005 Red Hat, Inc.

## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 51 Franklin Street, Suite 500, Boston, MA  02110-1335  USA

import datetime
import logging

# gnome-abrt
import application
import errors
import problems
from l10n import _

class ProblemSource(object):
    NEW_PROBLEM = 0
    DELETED_PROBLEM = 1
    CHANGED_PROBLEM = 2

    def __init__(self):
        self._observers = set()

    def get_items(self, problem_id, *args):
        pass

    def get_problems(self):
        pass

    def delete_problem(self, problem_id):
        pass

    def attach(self, observer):
        if not observer in self._observers:
            self._observers.add(observer)

    def detach(self, observer):
        try:
            self._observers.remove(observer)
        except ValueError as e:
            logging.debug(e.message)

    def notify(self, change_type=None, problem=None):
        logging.debug("{0} : Notify", self.__class__.__name__)
        for observer in self._observers:
            observer.changed(self, change_type, problem)

    def drop_cache(self):
        pass

class Problem:

    class Submission:
        URL = "URL"
        MSG = "MSG"
        BTHASH = "BTHASH"

        def __init__(self, title, rtype, data):
            self.title = title
            self.rtype = rtype
            self.data = data


    def __init__(self, problem_id, source):
        self.problem_id = problem_id
        self.source = source
        self.app = None
        self.submission = None
        self.data = self._get_initial_data(self.source)
        self._deleted = False

    def __str__(self):
        return self.problem_id

    def __eq__(self, other):
        if not other:
            return False
        elif isinstance(other, str):
            return self.problem_id == other
        elif isinstance(other, Problem):
            return self.problem_id == other.problem_id

        raise TypeError('Not allowed type in __eq__: ' + other.__class__.__name__)

    def __loaditems__(self, *args):
        if self._deleted:
            logging.debug("Accessing deleted problem '{0}'".format(self.problem_id))
            return {}

        items = self.source.get_items(self.problem_id, *args)
        for k, v in items.items():
            self.data[k] = v

        return items

    def __getitem__(self, item, cached=True):
        if item == 'date':
            return datetime.datetime.fromtimestamp(float(self['time']))
        elif item == 'application':
            return self.get_application()
        elif item == 'is_reported':
            return self.is_reported()
        elif item == 'submission':
            return self.get_submission()

        if cached and item in self.data:
            return self.data[item]

        loaded = self.__loaditems__(item)
        if item in loaded:
            return loaded[item]

        if item == 'count':
            return 1

        return None

    def _get_initial_data(self, source):
        return source.get_items(self.problem_id,
                                'component',
                                'executable',
                                'time',
                                'reason')

    def refresh(self):
        if self._deleted:
            logging.debug("Not refreshing deleted problem '{0}'".format(self.problem_id))
            return

        logging.debug("Refreshing problem '{0}'".format(self.problem_id))
        self.data = self._get_initial_data(self.source)
        self.submission = None
        self.source.notify(ProblemSource.CHANGED_PROBLEM, self)

    def delete(self):
        self._deleted = True
        try:
            self.source.delete_problem(self.problem_id)
        except Exception as e:
            logging.warning(_("Can't delete problem '{0}': '{1}'").format(self.problem_id, e.message))
            self._deleted = False

    def is_reported(self):
        return not self['reported_to'] is None

    def get_application(self):
        if not self.app:
            self.app = application.find_application(self['component'],
                                                    self['executable'])

        return self.app

    def get_submission(self):
        if not self.submission:
            self.submission = []
            if self['reported_to']:
                # Most common type of line in reported_to file
                # Bugzilla: URL=http://bugzilla.com/?=123456
                for l in self['reported_to'].split('\n'):
                    if len(l) == 0:
                        continue

                    p = []
                    for i in xrange(0,len(l)):
                        if l[i] == ':':
                            break
                        p.append(l[i])

                    p = ''.join(p)
                    i += 1

                    for i in xrange(i, len(l)):
                        if not l[i] == ' ':
                            break

                    r = []
                    for i in xrange(i, len(l)):
                        if l[i] == '=':
                            break
                        r.append(l[i])

                    r = ''.join(r)
                    i += 1

                    self.submission.append(
                        Problem.Submission(p, r, l[i:]))

        return self.submission


class MultipleSources(ProblemSource):

    def __init__(self, *args):
        super(MultipleSources, self).__init__()

        if len(args) == 0:
            raise ValueError("At least one source must be passed")

        self.sources = args

        class SourceObserver:
            def __init__(self, parent):
                self.parent = parent

            def changed(self, source, change_type=None, problem=None):
                self.parent.notify(change_type, problem)

        observer = SourceObserver(self)
        for s in self.sources:
            s.attach(observer)

        self._disable_notify = False

    def get_items(self, problem_id, *args):
        pass

    def get_problems(self):
        result = []
        for s in self.sources:
            result.extend(s.get_problems())

        return result

    def delete_problem(self, problem_id):
        pass

    def notify(self, change_type=None, problem=None):
        if self._disable_notify:
            return

        super(MultipleSources, self).notify(change_type, problem)

    def drop_cache(self):
        self._disable_notify = True

        try:
            for s in self.sources:
                s.drop_cache()
        finally:
            self._disable_notify = False

        self.notify()

class CachedSource(ProblemSource):

    def __init__(self):
        super(CachedSource, self).__init__()

        self._cache = None

    def get_problems(self):
        if not self._cache:
            self._cache = []
            for prblmid in self.impl_get_problems():
                try:
                    self._cache.append(self.create_new_problem(prblmid))
                except errors.InvalidProblem as e:
                    logging.warning(e.message)
                except errors.UnavailableSource as e:
                    logging.warning(e.message)

        return self._cache if self._cache else []

    def drop_cache(self):
        self._cache = None
        self.notify()

    def _insert_to_cache(self, problem):
        if self._cache:
            if problem in self._cache:
                raise errors.InvalidProblem(_("Problem '{0}' is already in the cache").format(problem.problem_id))

            self._cache.append(problem)

    def delete_problem(self, problem_id):
        if not self.impl_delete_problem(problem_id):
            return

        try:
            p = self._cache[self._cache.index(problem_id)]
            self._cache.remove(problem_id)
            self.notify(ProblemSource.DELETED_PROBLEM, p)
            return
        except ValueError as e:
            logging.warning(_('Not found in cache but deleted: {0}'), e.message)
            self._cache = None

        self.notify()

    def create_new_problem(self, problem_id):
        return problems.Problem(problem_id, self)

    def process_new_problem_id(self, problem_id):
        try:
            p = self.create_new_problem(problem_id)
            self._insert_to_cache(p)
            self.notify(ProblemSource.NEW_PROBLEM, p)
        except errors.InvalidProblem as e:
            logging.warning(_("Can't process '{0}': {1}").format(problem_id, e.message))
        except errors.UnavailableSource as e:
            logging.warning(_("Source failed on processing of '{0}': {1}").format(problem_id, e.message))
