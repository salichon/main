################################################################################
# Copyright (C) 2013-2014gempa GmbH
#
# FDSNEvent -- Implements the fdsnws-event Web service, see
#   http://www.fdsn.org/webservices/
#
# Feature notes:
#   - SeisComP does not distinguish between catalogs and contributors, but
#     supports agencyIDs. Hence, if specified, the value of the 'contributor'
#     parameter is mapped to the agencyID.
#   - origin and magnitude filter parameters are always applied to
#     preferred origin resp. preferred magnitude
#   - 'updateafter' request parameter not implemented
#   - additional request parameters:
#     - includepicks:    boolean, default: true,
#                        available only in combination with includearrivals=true
#     - includecomments: boolean, default: false
#     - formatted:       boolean, default: false
#   - additional values of request parameters:
#     - format
#       - standard:      [xml, text]
#       - additional:    [qml (=xml), qml-rt, sc3ml, csv]
#       - default:       xml
#
# Author:  Stephan Herrnkind
# Email:   herrnkind@gempa.de
################################################################################

from twisted.internet.threads import deferToThread
from twisted.web import http, server

import seiscomp.logging
from seiscomp.client import Application
from seiscomp.io import DatabaseInterface, Exporter
from seiscomp import datamodel

from .http import BaseResource
from .request import RequestOptions
from . import utils


DBMaxUInt = 18446744073709551615  # 2^64 - 1

VERSION = "1.2.6"

################################################################################


class _EventRequestOptions(RequestOptions):
    Exporters = {
        "xml": "qml1.2",
        "qml": "qml1.2",
        "qml-rt": "qml1.2rt",
        "sc3ml": "trunk",
        "csv": "csv",
    }
    VText = ["text"]
    VOrderBy = ["time", "time-asc", "magnitude", "magnitude-asc"]
    OutputFormats = list(Exporters) + VText

    PMinDepth = ["mindepth"]
    PMaxDepth = ["maxdepth"]
    PMinMag = ["minmagnitude", "minmag"]
    PMaxMag = ["maxmagnitude", "maxmag"]
    PMagType = ["magnitudetype", "magtype"]
    PEventType = ["eventtype"]

    PAllOrigins = ["includeallorigins", "allorigins"]
    PAllMags = ["includeallmagnitudes", "allmagnitudes", "allmags"]
    PArrivals = ["includearrivals", "allarrivals"]

    PEventID = ["eventid"]

    PLimit = ["limit"]
    POffset = ["offset"]
    POrderBy = ["orderby"]

    PContributor = ["contributor"]
    PCatalog = ["catalog"]
    PUpdateAfter = ["updateafter"]

    # non standard parameters
    PPicks = ["includepicks", "picks"]
    PFM = ["includefocalmechanism", "focalmechanism", "fm"]
    PAllFMs = ["includeallfocalmechanisms", "allfocalmechanisms", "allfms"]
    PStaMTs = ["includestationmts", "stationmts", "stamts"]
    PComments = ["includecomments", "comments"]
    PFormatted = ["formatted"]

    # SeisComP knows more event types than QuakeML. Types unknown to QuakeML
    # are mapped during the SeisComP to QuakeML conversion. Since the FDNSWS
    # standard defines both, the request and response type to be QuakeML some
    # extra SeisComP types need to be queried from the database.
    ExtraEventTypes = {
        datamodel.INDUCED_OR_TRIGGERED_EVENT: [datamodel.INDUCED_EARTHQUAKE],
        datamodel.OTHER_EVENT: [
            datamodel.DUPLICATE,
            datamodel.NOT_LOCATABLE,
            datamodel.OUTSIDE_OF_NETWORK_INTEREST,
        ],
    }

    GETParams = (
        RequestOptions.GETParams
        + RequestOptions.GeoParams
        + RequestOptions.OutputParams
        + PMinDepth
        + PMaxDepth
        + PMinMag
        + PMaxMag
        + PMagType
        + PEventType
        + PAllOrigins
        + PAllMags
        + PArrivals
        + PEventID
        + PLimit
        + POffset
        + POrderBy
        + PContributor
        + PCatalog
        + PUpdateAfter
        + PPicks
        + PFM
        + PAllFMs
        + PStaMTs
        + PComments
        + PFormatted
    )

    # ---------------------------------------------------------------------------
    class Depth:
        def __init__(self):
            self.min = None
            self.max = None

    # ---------------------------------------------------------------------------
    class Magnitude:
        def __init__(self):
            self.min = None
            self.max = None
            self.type = None

    # ---------------------------------------------------------------------------
    def __init__(self):
        super().__init__()

        self.service = "fdsnws-event"

        self.depth = None
        self.mag = None
        self.eventTypes = set()  # SeisComP numeric event type ids, -1 is used
        # for empty event types

        self.allOrigins = None
        self.allMags = None
        self.arrivals = None

        self.limit = None  # event limit, if defined: min 1
        self.offset = None  # start at specific event count position,
        # the spec uses a weird offset definition
        # where an offset of '1' returns the first
        # element, not the second one
        self.orderBy = None  # [time, time-asc, magnitude, magnitude-asc]
        self.catalogs = []  # not supported
        self.contributors = []  # mapped to agency id
        self.updatedAfter = None

        self.eventIDs = []  # specific event filter may not be
        # combined with above filter criteria

        # non standard parameters
        self.comments = None
        self.formatted = None
        self.picks = None
        self.fm = None
        self.allFMs = None
        self.staMTs = None

    # ---------------------------------------------------------------------------
    def parse(self):
        self.parseTime()
        self.parseGeo()
        self.parseOutput()

        # depth
        d = self.Depth()
        d.min = self.parseFloat(self.PMinDepth)
        d.max = self.parseFloat(self.PMaxDepth)
        if d.min is not None and d.max is not None and d.min > d.max:
            raise ValueError(f"{self.PMinDepth[0]} exceeds {self.PMaxDepth[0]}")
        if d.min is not None or d.max:
            self.depth = d

        # magnitude
        m = self.Magnitude()
        m.min = self.parseFloat(self.PMinMag)
        m.max = self.parseFloat(self.PMaxMag)
        if m.min is not None and m.max is not None and m.min > m.max:
            raise ValueError(f"{self.PMinMag[0]} exceeds {self.PMaxMag[0]}")
        key, m.type = self.getFirstValue(self.PMagType)
        if m.min is not None or m.max is not None or m.type is not None:
            self.mag = m

        # event type
        for eType in self.getListValues(self.PEventType):
            if not eType:
                continue

            t = eType.lower()
            if t == "unknown":
                self.eventTypes.add(-1)
            else:
                try:
                    scType = datamodel.QMLTypeMapper.EventTypeFromString(t)
                    self.eventTypes.add(scType)
                    # SeisComP knows more event types than QuakeML. Types
                    # unknown to QuakeML are mapped during the SeisComP to
                    # QuakeML conversion. Since the FDNSWS standard defines
                    # both, the request and response type to be QuakeML some
                    # extra SeisComP types need to be queried from the
                    # database.
                    if scType in self.ExtraEventTypes:
                        self.eventTypes.update(self.ExtraEventTypes[scType])
                except ValueError as e:
                    raise ValueError(f"'{t}' is not a valid QuakeML event type") from e

        # output components
        self.allOrigins = self.parseBool(self.PAllOrigins)
        self.allMags = self.parseBool(self.PAllMags)
        self.arrivals = self.parseBool(self.PArrivals)
        self.picks = self.parseBool(self.PPicks)
        self.fm = self.parseBool(self.PFM)
        self.allFMs = self.parseBool(self.PAllFMs)
        self.staMTs = self.parseBool(self.PStaMTs)
        self.comments = self.parseBool(self.PComments)

        # limit, offset, orderBy, updatedAfter
        self.limit = self.parseInt(self.PLimit, 1, DBMaxUInt)
        self.offset = self.parseInt(self.POffset, 1, DBMaxUInt)
        if self.offset is not None:
            # the offset is decremented by one since spec uses a weird offset
            # where an offset of '1' should return the first element instead of
            # the second one
            self.offset -= 1
        key, value = self.getFirstValue(self.POrderBy)
        if value is not None:
            if value in self.VOrderBy:
                self.orderBy = value
            else:
                self.raiseValueError(key)

        # catalogs and contributors
        self.catalogs = self.getValues(self.PCatalog)
        self.contributors = self.getValues(self.PContributor)
        self.updatedAfter = self.parseTimeStr(self.PUpdateAfter)

        # eventID(s)
        filterParams = (
            self.time
            or self.geo
            or self.depth
            or self.mag
            or self.limit is not None
            or self.offset is not None
            or self.orderBy
            or self.catalogs
            or self.contributors
            or self.updatedAfter
        )
        self.eventIDs = self.getValues(self.PEventID)
        # eventID, MUST NOT be combined with above parameters
        if filterParams and self.eventIDs:
            raise ValueError(
                f"invalid mixture of parameters, the parameter 'self.PEventID[0]' may "
                f"only be combined with: {self.PAllOrigins[0]}, {self.PAllMags[0]}, "
                f"{self.PArrivals[0]}, {self.PPicks[0]}, {self.PFM[0]}, "
                f"{self.PAllFMs[0]}, {self.PStaMTs[0]}, {self.PComments[0]}"
            )

        # format XML
        self.formatted = self.parseBool(self.PFormatted)


################################################################################
class FDSNEvent(BaseResource):
    isLeaf = True

    # ---------------------------------------------------------------------------
    def __init__(
        self,
        hideAuthor=False,
        hideComments=False,
        evaluationMode=None,
        eventTypeWhitelist=None,
        eventTypeBlacklist=None,
        formatList=None,
    ):
        super().__init__(VERSION)

        self._hideAuthor = hideAuthor
        self._hideComments = hideComments
        self._evaluationMode = evaluationMode
        self._eventTypeWhitelist = eventTypeWhitelist
        self._eventTypeBlacklist = eventTypeBlacklist
        self._formatList = formatList

    # ---------------------------------------------------------------------------
    def render_OPTIONS(self, req):
        req.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS")
        req.setHeader(
            "Access-Control-Allow-Headers",
            "Accept, Content-Type, X-Requested-With, Origin",
        )
        req.setHeader("Content-Type", "text/plain; charset=utf-8")
        return ""

    # ---------------------------------------------------------------------------
    def render_GET(self, req):
        # Parse and validate GET parameters
        ro = _EventRequestOptions()
        try:
            ro.parseGET(req.args)
            ro.parse()
        except ValueError as e:
            seiscomp.logging.warning(str(e))
            return self.renderErrorPage(req, http.BAD_REQUEST, str(e), ro)

        # Catalog filter is not supported
        if ro.catalogs:
            msg = "catalog filter not supported"
            return self.renderErrorPage(req, http.BAD_REQUEST, msg, ro)

        if ro.comments and self._hideComments:
            msg = "including of comments not supported"
            return self.renderErrorPage(req, http.BAD_REQUEST, msg, ro)

        # updateafter not implemented
        if ro.updatedAfter:
            msg = "filtering based on update time not supported"
            return self.renderErrorPage(req, http.BAD_REQUEST, msg, ro)

        if self._formatList is not None and ro.format not in self._formatList:
            msg = f"output format '{ro.format}' not available"
            return self.renderErrorPage(req, http.BAD_REQUEST, msg, ro)

        # Exporter, 'None' is used for text output
        if ro.format in ro.VText:
            exp = None
        else:
            exp = Exporter.Create(ro.Exporters[ro.format])
            if exp:
                exp.setFormattedOutput(bool(ro.formatted))
            else:
                msg = (
                    f"output format '{ro.format}' not available, export module "
                    f"'{ro.Exporters[ro.format]}' could not be loaded."
                )
                return self.renderErrorPage(req, http.BAD_REQUEST, msg, ro)

        # Create database query
        db = DatabaseInterface.Open(Application.Instance().databaseURI())
        if db is None:
            msg = "could not connect to database"
            return self.renderErrorPage(req, http.SERVICE_UNAVAILABLE, msg, ro)

        dbq = datamodel.DatabaseQuery(db)

        # Process request in separate thread
        d = deferToThread(self._processRequest, req, ro, dbq, exp)
        req.notifyFinish().addErrback(utils.onCancel, d)
        d.addBoth(utils.onFinish, req)

        # The request is handled by the deferred object
        return server.NOT_DONE_YET

    # ---------------------------------------------------------------------------
    @staticmethod
    def _removeAuthor(obj):
        try:
            ci = obj.creationInfo()
            ci.setAuthor("")
            ci.setAuthorURI("")
        except ValueError:
            pass

    # ---------------------------------------------------------------------------
    def _loadComments(self, dbq, obj):
        cnt = dbq.loadComments(obj)
        if self._hideAuthor:
            for iComment in range(cnt):
                self._removeAuthor(obj.comment(iComment))
        return cnt

    # ---------------------------------------------------------------------------
    def _processRequestExp(self, req, ro, dbq, exp, ep):
        objCount = ep.eventCount()
        maxObj = Application.Instance()._queryObjects  # pylint: disable=W0212

        if not self.checkObjects(req, objCount, maxObj):
            return False

        pickIDs = set()
        if ro.picks is None:
            ro.picks = True

        # add related information
        for iEvent in range(ep.eventCount()):
            if req._disconnected:  # pylint: disable=W0212
                return False
            e = ep.event(iEvent)
            if self._hideAuthor:
                self._removeAuthor(e)

            originIDs = set()
            magIDs = set()
            magIDs.add(e.preferredMagnitudeID())

            # eventDescriptions and comments
            objCount += dbq.loadEventDescriptions(e)
            if ro.comments:
                objCount += self._loadComments(dbq, e)
            if not self.checkObjects(req, objCount, maxObj):
                return False

            # origin references: either all or preferred only
            dbIter = dbq.getObjects(e, datamodel.OriginReference.TypeInfo())
            for obj in dbIter:
                oRef = datamodel.OriginReference.Cast(obj)
                if oRef is None:
                    continue
                if ro.allOrigins:
                    e.add(oRef)
                    originIDs.add(oRef.originID())
                elif oRef.originID() == e.preferredOriginID():
                    e.add(oRef)
                    originIDs.add(oRef.originID())
                    dbIter.close()

            objCount += e.originReferenceCount()

            # focalMechanism references: either none, preferred only or all
            if ro.fm or ro.allFMs:
                dbIter = dbq.getObjects(e, datamodel.FocalMechanismReference.TypeInfo())
                for obj in dbIter:
                    fmRef = datamodel.FocalMechanismReference.Cast(obj)
                    if fmRef is None:
                        continue
                    if ro.allFMs:
                        e.add(fmRef)
                    elif fmRef.focalMechanismID() == e.preferredFocalMechanismID():
                        e.add(fmRef)
                        dbIter.close()

            objCount += e.focalMechanismReferenceCount()

            if not self.checkObjects(req, objCount, maxObj):
                return False

            # focal mechanisms: process before origins to add derived origin to
            # originID list since it may be missing from origin reference list
            for iFMRef in range(e.focalMechanismReferenceCount()):
                if req._disconnected:  # pylint: disable=W0212
                    return False
                fmID = e.focalMechanismReference(iFMRef).focalMechanismID()
                obj = dbq.getObject(datamodel.FocalMechanism.TypeInfo(), fmID)
                fm = datamodel.FocalMechanism.Cast(obj)
                if fm is None:
                    continue

                ep.add(fm)
                objCount += 1
                if self._hideAuthor:
                    self._removeAuthor(fm)

                # comments
                if ro.comments:
                    objCount += self._loadComments(dbq, fm)

                # momentTensors
                objCount += dbq.loadMomentTensors(fm)

                if not self.checkObjects(req, objCount, maxObj):
                    return False

                for iMT in range(fm.momentTensorCount()):
                    mt = fm.momentTensor(iMT)

                    originIDs.add(mt.derivedOriginID())
                    magIDs.add(mt.momentMagnitudeID())

                    if self._hideAuthor:
                        self._removeAuthor(mt)

                    if ro.comments:
                        for _ in range(fm.momentTensorCount()):
                            objCount += self._loadComments(dbq, mt)

                    objCount += dbq.loadDataUseds(mt)
                    objCount += dbq.loadMomentTensorPhaseSettings(mt)
                    if ro.staMTs:
                        objCount += dbq.loadMomentTensorStationContributions(mt)
                        for iStaMT in range(mt.momentTensorStationContributionCount()):
                            objCount += dbq.load(
                                mt.momentTensorStationContribution(iStaMT)
                            )

                    if not self.checkObjects(req, objCount, maxObj):
                        return False

            # find ID of origin containing preferred Magnitude
            if e.preferredMagnitudeID():
                obj = dbq.getObject(
                    datamodel.Magnitude.TypeInfo(), e.preferredMagnitudeID()
                )
                m = datamodel.Magnitude.Cast(obj)
                if m is not None:
                    oID = dbq.parentPublicID(m)
                    if oID:
                        originIDs.add(oID)

            # origins
            for oID in sorted(originIDs):
                if req._disconnected:  # pylint: disable=W0212
                    return False
                obj = dbq.getObject(datamodel.Origin.TypeInfo(), oID)
                o = datamodel.Origin.Cast(obj)
                if o is None:
                    continue

                ep.add(o)
                objCount += 1
                if self._hideAuthor:
                    self._removeAuthor(o)

                # comments
                if ro.comments:
                    objCount += self._loadComments(dbq, o)
                if not self.checkObjects(req, objCount, maxObj):
                    return False

                # magnitudes
                dbIter = dbq.getObjects(oID, datamodel.Magnitude.TypeInfo())
                for obj in dbIter:
                    mag = datamodel.Magnitude.Cast(obj)
                    if mag is None:
                        continue
                    if ro.allMags:
                        o.add(mag)
                    elif mag.publicID() in magIDs:
                        o.add(mag)
                        dbIter.close()

                    if self._hideAuthor:
                        self._removeAuthor(mag)

                objCount += o.magnitudeCount()
                if ro.comments:
                    for iMag in range(o.magnitudeCount()):
                        objCount += self._loadComments(dbq, o.magnitude(iMag))
                if not self.checkObjects(req, objCount, maxObj):
                    return False

                # TODO station magnitudes, amplitudes
                # - added pick id for each pick referenced by amplitude

                # arrivals
                if ro.arrivals:
                    objCount += dbq.loadArrivals(o)
                    if self._hideAuthor:
                        for iArrival in range(o.arrivalCount()):
                            self._removeAuthor(o.arrival(iArrival))

                    # collect pick IDs if requested
                    if ro.picks:
                        for iArrival in range(o.arrivalCount()):
                            pickIDs.add(o.arrival(iArrival).pickID())

                if not self.checkObjects(req, objCount, maxObj):
                    return False

        # picks
        if pickIDs:
            objCount += len(pickIDs)
            if not self.checkObjects(req, objCount, maxObj):
                return False

            for pickID in sorted(pickIDs):
                obj = dbq.getObject(datamodel.Pick.TypeInfo(), pickID)
                pick = datamodel.Pick.Cast(obj)
                if pick is not None:
                    if self._hideAuthor:
                        self._removeAuthor(pick)
                    if ro.comments:
                        objCount += self._loadComments(dbq, pick)
                    ep.add(pick)
                if not self.checkObjects(req, objCount, maxObj):
                    return False

        # write response
        sink = utils.Sink(req)
        if not exp.write(sink, ep):
            return False
        seiscomp.logging.debug(
            f"{ro.service}: returned {ep.eventCount()} events and {ep.originCount()} "
            f"origins (total objects/bytes: {objCount}/{sink.written})"
        )
        utils.accessLog(req, ro, http.OK, sink.written, None)
        return True

    # ---------------------------------------------------------------------------
    def _processRequestText(self, req, ro, dbq, ep):
        lineCount = 0

        line = (
            "#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|"
            "Contributor|ContributorID|MagType|Magnitude|MagAuthor|"
            "EventLocationName|EventType\n"
        )
        df = "%FT%T.%f"
        utils.writeTS(req, line)
        byteCount = len(line)

        # add related information
        for iEvent in range(ep.eventCount()):
            e = ep.event(iEvent)
            eID = e.publicID()

            # query for preferred origin
            obj = dbq.getObject(datamodel.Origin.TypeInfo(), e.preferredOriginID())
            o = datamodel.Origin.Cast(obj)
            if o is None:
                seiscomp.logging.warning(
                    f"preferred origin of event '{eID}' not found: "
                    f"{e.preferredOriginID()}"
                )
                continue

            # depth
            try:
                depth = str(o.depth().value())
            except ValueError:
                depth = ""

            # author
            if self._hideAuthor:
                author = ""
            else:
                try:
                    author = o.creationInfo().author()
                except ValueError:
                    author = ""

            # contributor
            try:
                contrib = e.creationInfo().agencyID()
            except ValueError:
                contrib = ""

            # query for preferred magnitude (if any)
            mType, mVal, mAuthor = "", "", ""
            if e.preferredMagnitudeID():
                obj = dbq.getObject(
                    datamodel.Magnitude.TypeInfo(), e.preferredMagnitudeID()
                )
                m = datamodel.Magnitude.Cast(obj)
                if m is not None:
                    mType = m.type()
                    mVal = str(m.magnitude().value())
                    if self._hideAuthor:
                        mAuthor = ""
                    else:
                        try:
                            mAuthor = m.creationInfo().author()
                        except ValueError:
                            pass

            # event description
            dbq.loadEventDescriptions(e)
            region = ""
            for i in range(e.eventDescriptionCount()):
                ed = e.eventDescription(i)
                if ed.type() == datamodel.REGION_NAME:
                    region = ed.text()
                    break

            # event type
            try:
                eType = datamodel.QMLTypeMapper.EventTypeToString(e.type())
            except ValueError:
                eType = ""

            if req._disconnected:  # pylint: disable=W0212
                return False
            line = (
                f"{eID}|{o.time().value().toString(df)}|{o.latitude().value()}|"
                f"{o.longitude().value()}|{depth}|{author}||{contrib}|{eID}|"
                f"{mType}|{mVal}|{mAuthor}|{region}|{eType}\n"
            )
            lineBin = utils.b_str(line)
            utils.writeTSBin(req, lineBin)
            lineCount += 1
            byteCount += len(lineBin)

        # write response
        seiscomp.logging.debug(
            f"{ro.service}: returned {lineCount} events (total bytes: {byteCount})"
        )
        utils.accessLog(req, ro, http.OK, byteCount, None)
        return True

    # ---------------------------------------------------------------------------
    def _processRequest(self, req, ro, dbq, exp):
        if req._disconnected:  # pylint: disable=W0212
            return False

        datamodel.PublicObject.SetRegistrationEnabled(False)

        # query event(s)
        ep = datamodel.EventParameters()
        if ro.eventIDs:
            for eID in ro.eventIDs:
                obj = dbq.getEventByPublicID(eID)
                e = datamodel.Event.Cast(obj)
                if not e:
                    continue

                if self._eventTypeWhitelist or self._eventTypeBlacklist:
                    eType = -1
                    try:
                        eType = e.type()
                    except ValueError:
                        pass
                    if (
                        self._eventTypeWhitelist
                        and eType not in self._eventTypeWhitelist
                    ):
                        continue
                    if self._eventTypeBlacklist and eType in self._eventTypeBlacklist:
                        continue

                if self._evaluationMode is not None:
                    obj = dbq.getObject(
                        datamodel.Origin.TypeInfo(), e.preferredOriginID()
                    )
                    o = datamodel.Origin.Cast(obj)
                    try:
                        if o is None or o.evaluationMode() != self._evaluationMode:
                            continue
                    except ValueError:
                        continue

                ep.add(e)
        else:
            self._findEvents(ep, ro, dbq)

        if ep.eventCount() == 0:
            msg = "no matching events found"
            self.writeErrorPage(req, http.NO_CONTENT, msg, ro)
            return True

        seiscomp.logging.debug(f"events found: {ep.eventCount()}")

        if ro.format == "csv" or not exp:
            req.setHeader("Content-Type", "text/plain; charset=utf-8")
        else:
            req.setHeader("Content-Type", "application/xml; charset=utf-8")

        if exp:
            return self._processRequestExp(req, ro, dbq, exp, ep)

        return self._processRequestText(req, ro, dbq, ep)

    # ---------------------------------------------------------------------------
    def _findEvents(self, ep, ro, dbq):
        db = Application.Instance().database()

        def _T(name):
            return db.convertColumnName(name)

        def _time(time):
            return db.timeToString(time)

        orderByMag = ro.orderBy and ro.orderBy.startswith("magnitude")
        reqMag = ro.mag or orderByMag
        reqMagType = ro.mag and ro.mag.type
        reqDist = ro.geo and ro.geo.bCircle
        colPID = _T("publicID")
        colTime = _T("time_value")
        colMag = _T("magnitude_value")
        colLat = _T("latitude_value")
        colLon = _T("longitude_value")
        if orderByMag:
            colOrderBy = f"m.{colMag}"
        else:
            colOrderBy = f"o.{colTime}"

        bBox = None
        if ro.geo:
            if ro.geo.bBox:
                bBox = ro.geo.bBox
            else:
                bBox = ro.geo.bCircle.calculateBBox()

        # SELECT --------------------------------
        q = f"SELECT DISTINCT pe.{colPID} AS {colPID}, e.*, {colOrderBy} AS colOrderBy"
        if reqDist:  # Great circle distance calculated by Haversine formula
            c = ro.geo.bCircle
            q += (
                ", DEGREES(ACOS("
                f"COS(RADIANS(o.{colLat})) * COS(RADIANS({c.lat})) * "
                f"COS(RADIANS(o.{colLon}) - RADIANS({c.lon})) + "
                f"SIN(RADIANS(o.{colLat})) * SIN(RADIANS({c.lat})))) AS distance"
            )

        # FROM ----------------------------------
        q += " FROM Event AS e, PublicObject AS pe" ", Origin AS o, PublicObject AS po"
        if reqMag:
            q += ", Magnitude AS m"
            if not reqMagType:
                # the preferred magnitude is used if not specific magnitude type
                # is requested
                q += ", PublicObject AS pm"

        # WHERE ---------------------------------
        q += " WHERE e._oid = pe._oid"

        # event type white list filter, defined via configuration and/or request
        # parameters
        types = None
        if self._eventTypeWhitelist and ro.eventTypes:
            types = self._eventTypeWhitelist.intersection(ro.eventTypes)
            if not types:
                seiscomp.logging.debug(
                    "all requested event types filtered by configured event "
                    "type white list"
                )
                return
        elif self._eventTypeWhitelist:
            types = self._eventTypeWhitelist
        elif ro.eventTypes:
            types = ro.eventTypes
        if types is not None:
            allowNull = -1 in types
            types = [x for x in types if x >= 0]

            typesStr = "', '".join(datamodel.EEventTypeNames.name(x) for x in types)
            etqIn = f"e.{_T('type')} IN ('{typesStr}')"
            if allowNull:
                etqNull = f"e.{_T('type')} is NULL"
                if types:
                    q += f" AND ({etqNull} OR {etqIn})"
                else:
                    q += f" AND {etqNull}"
            else:
                q += f" AND {etqIn}"

        # event type black list filter, defined in configuration
        if self._eventTypeBlacklist:
            allowNull = -1 not in self._eventTypeBlacklist
            types = [x for x in self._eventTypeBlacklist if x >= 0]

            typesStr = "', '".join(datamodel.EEventTypeNames.name(x) for x in types)
            etqNotIn = f"e.{_T('type')} NOT IN ('{typesStr}')"
            if allowNull:
                etqNull = f"e.{_T('type')} is NULL"
                if types:
                    q += f" AND ({etqNull} OR {etqNotIn})"
                else:
                    q += f" AND {etqNull}"
            else:
                q += f" AND {etqNotIn}"

        # event agency id filter
        if ro.contributors:
            contribStr = "', '".join(ro.contributors).upper()
            q += (
                f" AND e.{_T('creationinfo_used')}"
                f" AND UPPER(e.{_T('creationinfo_agencyid')}) IN('{contribStr}')"
            )

        # origin information filter
        q += f" AND o._oid = po._oid AND po.{colPID} = e.{_T('preferredOriginID')}"

        # evaluation mode config parameter
        if self._evaluationMode is not None:
            q += (
                f" AND o.{_T('evaluationMode')} = "
                f"'{datamodel.EEvaluationModeNames.name(self._evaluationMode)}'"
            )

        # time
        if ro.time:
            colTimeMS = _T("time_value_ms")
            if ro.time.start is not None:
                t = _time(ro.time.start)
                ms = ro.time.start.microseconds()
                q += (
                    f" AND (o.{colTime} > '{t}' OR ("
                    f"o.{colTime} = '{t}' AND o.{colTimeMS} >= {ms}))"
                )
            if ro.time.end is not None:
                t = _time(ro.time.end)
                ms = ro.time.end.microseconds()
                q += (
                    f" AND (o.{colTime} < '{t}' OR ("
                    f"o.{colTime} = '{t}' AND o.{colTimeMS} <= {ms}))"
                )

        # bounding box
        if bBox:
            if bBox.minLat is not None:
                q += f" AND o.{colLat} >= {bBox.minLat}"
            if bBox.maxLat is not None:
                q += f" AND o.{colLat} <= {bBox.maxLat}"
            if bBox.dateLineCrossing():
                q += f" AND (o.{colLon} >= {bBox.minLon} OR o.{colLon} <= {bBox.maxLo})"
            else:
                if bBox.minLon is not None:
                    q += f" AND o.{colLon} >= {bBox.minLon}"
                if bBox.maxLon is not None:
                    q += f" AND o.{colLon} <= {bBox.maxLon}"

        # depth
        if ro.depth:
            q += f" AND o.{_T('depth_used')}"
            colDepth = _T("depth_value")
            if ro.depth.min is not None:
                q += f" AND o.{colDepth} >= {ro.depth.min}"
            if ro.depth.max is not None:
                q += f" AND o.{colDepth} <= {ro.depth.max}"

        # updated after
        if ro.updatedAfter:
            t = _time(ro.updatedAfter)
            ms = ro.updatedAfter.microseconds()
            colCTime = _T("creationinfo_creationtime")
            colCTimeMS = _T("creationinfo_creationtime_ms")
            colMTime = _T("creationinfo_modificationtime")
            colMTimeMS = _T("creationinfo_modificationtime_ms")
            tFilter = "(o.%s > '%s' OR (o.%s = '%s' AND o.%s > %i))"

            q += " AND ("
            q += f"{tFilter % (colCTime, t, colCTime, t, colCTimeMS, ms)} OR "
            q += f"{tFilter % (colMTime, t, colMTime, t, colMTimeMS, ms)})"

        # magnitude information filter
        if reqMag:
            if ro.mag and ro.mag.min is not None:
                q += f" AND m.{colMag} >= {ro.mag.min}"
            if ro.mag and ro.mag.max is not None:
                q += f" AND m.{colMag} <= {ro.mag.max}"

            # default case, no magnitude type filter:
            # join magnitude table on preferred magnitude id of event
            if not reqMagType:
                q += (
                    f" AND m._oid = pm._oid"
                    f" AND pm.{colPID} = e.{_T('preferredMagnitudeID')}"
                )

            # magnitude type filter:
            # Specific mag type is searched in magnitudes of preferred origin or
            # in derived origin of moment tensors of preferred focal mechanism.
            else:
                q += (
                    f" AND m.{_T('type')} = '{dbq.toString(ro.mag.type)}'"
                    " AND m._parent_oid "
                )

                # For performance reasons the query is split in two parts
                # combined with a UNION statement. The subsequent ORDER BY,
                # LIMIT/OFFSET or distance subquery is carried out on the entire
                # UNION result set.
                q += (
                    f"= po._oid UNION {q} IN ("
                    "SELECT pdo._oid FROM PublicObject pfm, MomentTensor mt, "
                    "PublicObject pdo WHERE "
                    f"pfm.{colPID} = e.{_T('preferredFocalMechanismID')} AND "
                    f"mt._parent_oid = pfm._oid AND "
                    f"pdo.{colPID} = mt.{_T('derivedOriginID')})"
                )

        # ORDER BY ------------------------------
        q += " ORDER BY colOrderBy "
        if ro.orderBy and ro.orderBy.endswith("-asc"):
            q += "ASC"
        else:
            q += "DESC"

        # SUBQUERY distance (optional) ----------
        if reqDist:
            q = f"SELECT * FROM ({q}) AS subquery WHERE distance "
            c = ro.geo.bCircle
            if c.minRad is not None:
                q += f">= {c.minRad}"
            if c.maxRad is not None:
                if c.minRad is not None:
                    q += " AND distance "
                q += f"<= {c.maxRad}"

        # LIMIT/OFFSET --------------------------
        if ro.limit is not None or ro.offset is not None:
            # Postgres allows to omit the LIMIT parameter for offsets, MySQL
            # does not. According to the MySQL manual a very large number should
            # be used for this case.
            limit = DBMaxUInt
            if ro.limit is not None:
                limit = ro.limit
            q += f" LIMIT {limit}"
            if ro.offset is not None:
                q += f" OFFSET {ro.offset}"

        seiscomp.logging.debug(f"event query: {q}")

        for e in dbq.getObjectIterator(q, datamodel.Event.TypeInfo()):
            ep.add(datamodel.Event.Cast(e))


# vim: ts=4 et
